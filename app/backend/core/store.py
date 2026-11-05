"""Tier 1 冻结快照的只读访问层。

竖切片阶段直接读 JSONL（423 行、约 577KB，常驻内存完全可接受）。
SQLite 化属于 M2 的构建期产物，接口保持不变，故此处不预留空实现。
"""

import json
import sqlite3
from collections import Counter
from pathlib import Path

from . import sqlite_compat
from .errors import ApiError

CJK_START, CJK_END = "\u3400", "\u9fff"


def _cjk_only(text: str) -> str:
    return "".join(ch for ch in (text or "") if CJK_START <= ch <= CJK_END)


def _poet_name(work: dict) -> str:
    poet = work.get("poet")
    if isinstance(poet, dict):
        return poet.get("name") or ""
    return work.get("poet_name") or str(poet or "")


class Store:
    def __init__(self, snapshot_path: Path, index_path: Path | None = None):
        self.snapshot_path = snapshot_path
        self.index_path = index_path
        self.works: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self._index: list[dict] = []
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        if not self.snapshot_path.is_file():
            raise ApiError(
                "KNOWLEDGE_UNAVAILABLE",
                "未找到 Tier 1 冻结快照",
                {"expected_path": str(self.snapshot_path)},
            )
        rows = []
        with self.snapshot_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        self.works = rows
        self.by_id = {int(work["id"]): work for work in rows if work.get("id") is not None}
        self._index = [
            {
                "work": work,
                "text": _cjk_only(work.get("text")),
                "poet": _cjk_only(_poet_name(work)),
                "poet_raw": _poet_name(work),
                "tune": _cjk_only(work.get("tune_pattern")),
            }
            for work in rows
        ]
        self._loaded = True

    def all(self) -> list[dict]:
        self.load()
        return self.works

    def get(self, work_id: int | str) -> dict:
        self.load()
        try:
            key = int(work_id)
        except (TypeError, ValueError):
            raise ApiError(
                "INVALID_INPUT",
                "作品编号格式不对",
                {
                    "work_id": work_id,
                    "hint": "官方层用整数 id；同源层 t2a_/t2b_/t2c_/t2d_ 前缀 id 需走带 settings 的数据层",
                },
            )
        work = self.by_id.get(key)
        if work is None:
            raise ApiError("NOT_FOUND", "作品不存在或未发布", {"work_id": work_id})
        return work

    def resolve(self, label: str, poet: str, tune: str, first: str) -> dict:
        self.load()
        first_norm = _cjk_only(first)
        poet_norm = _cjk_only(poet)
        tune_norm = _cjk_only(tune)
        candidates = [
            item["work"]
            for item in self._index
            if first_norm
            and first_norm in item["text"]
            and (
                poet_norm == item["poet"]
                or item["poet_raw"].startswith(poet + "《")
            )
            and (not tune_norm or tune_norm == item["tune"])
        ]
        if len(candidates) != 1:
            raise ApiError(
                "INSUFFICIENT_DATA",
                "锚点无法唯一定位到作品",
                {"label": label, "poet": poet, "tune": tune, "candidates": len(candidates)},
            )
        return candidates[0]


    def _index_connection(self):
        if self.index_path and self.index_path.is_file():
            uri = "file:%s?mode=ro" % self.index_path.as_posix()
            # 走兼容层：目标机系统 SQLite 可能过旧（无 FTS5 trigram），
            # 此时自动改用 pysqlite3（自带新版 SQLite）。
            connection = sqlite_compat.connect(uri, read_only=True)
            connection.row_factory = sqlite_compat.Row
            return connection
        return None

    def _search_memory(self, query: str, period, tune, poet, limit: int) -> dict:
        items = []
        for item in self._index:
            work = item["work"]
            if period and (work.get("period_label") or "") != period:
                continue
            if tune and (work.get("tune_pattern") or "") != tune:
                continue
            if poet and poet not in item["poet_raw"]:
                continue
            if query not in item["text"]:
                continue
            lines = [
                {"line_no": line["line_no"], "text": line["text"]}
                for line in (work.get("prosody") or {}).get("lines") or []
                if query in (line.get("text") or "")
            ]
            items.append(
                {
                    "id": work.get("id"),
                    "title": work.get("title"),
                    "tune": work.get("tune_pattern"),
                    "period": work.get("period_label"),
                    "poet": item["poet_raw"],
                    "matched": lines[:3],
                    "hits": len(lines),
                }
            )
        items.sort(key=lambda entry: (-entry["hits"], entry["id"]))
        return {"q": query, "mode": "substring", "items": items[:limit], "total": len(items), "index": "memory"}

    def search(self, query: str, period=None, tune=None, poet=None, limit: int = 20) -> dict:
        """1–2 字走一元/二元倒排，≥3 字走 FTS5 trigram；索引缺失时回退内存子串。"""
        normalized = _cjk_only(query)
        if not normalized:
            raise ApiError("INVALID_INPUT", "检索词需包含汉字")
        self.load()
        connection = self._index_connection()
        if connection is None:
            return self._search_memory(normalized, period, tune, poet, limit)
        try:
            if len(normalized) >= 3:
                mode = "trigram"
                sql = "SELECT w.id AS id FROM works_fts JOIN works w ON w.id = works_fts.rowid WHERE works_fts MATCH ?"
                params: list = [normalized]
            else:
                mode = "bigram" if len(normalized) == 2 else "unigram"
                sql = (
                    "SELECT DISTINCT g.work_id AS id FROM grams g JOIN works w ON w.id = g.work_id "
                    "WHERE g.gram = ? AND g.n = ?"
                )
                params = [normalized, len(normalized)]
            if period:
                sql += " AND w.period = ?"
                params.append(period)
            if tune:
                sql += " AND w.tune = ?"
                params.append(tune)
            if poet:
                sql += " AND w.poet LIKE ?"
                params.append("%" + poet + "%")

            try:
                ids = [row["id"] for row in connection.execute(sql, params)]
            except sqlite3.Error as error:
                # ≥3 字走 FTS5 trigram；若目标机既没有新版 SQLite 也没有 pysqlite3，
                # 会在这里报 "no such tokenizer: trigram"。此时回退到内存子串匹配，
                # 功能可用（结果一致，只是慢），不把 500 抛给用户。
                if mode != "trigram":
                    raise
                result = self._search_memory(normalized, period, tune, poet, limit)
                result["index"] = "memory-fallback"
                result["fallback_reason"] = str(error)
                return result
            items = []
            for work_id in ids[: max(limit * 3, 30)]:
                lines = [
                    {"line_no": row["line_no"], "text": row["text"]}
                    for row in connection.execute(
                        "SELECT line_no, text FROM lines WHERE work_id = ? AND text LIKE ? ORDER BY line_no",
                        (work_id, "%" + normalized + "%"),
                    )
                ]
                work = self.by_id.get(work_id) or {}
                items.append(
                    {
                        "id": work_id,
                        "title": work.get("title"),
                        "tune": work.get("tune_pattern"),
                        "period": work.get("period_label"),
                        "poet": _poet_name(work),
                        "matched": lines[:3],
                        "hits": len(lines),
                        # 层级标签：官方层的逐句计数由官方给出，与同源层要分得清
                        "tier_label": "官方标注",
                        "source": "Tier 1",
                    }
                )
            items.sort(key=lambda entry: (-entry["hits"], entry["id"]))
            return {"q": normalized, "mode": mode, "items": items[:limit], "total": len(ids), "index": "sqlite"}
        finally:
            connection.close()

    def tune_stats(self, tune: str) -> dict:
        """同调统计：按词牌给作品数、句数分布与仄比中位数（规划 B2 检索板块要求）。

        口径：Tier 1 官方快照的逐句计数；同一词牌可能有多套句式，因此句数分布
        按"该词牌下出现过的句数及其次数"给出，不给单一数字。
        """
        self.load()
        name = (tune or "").strip()
        if not name:
            raise ApiError("INVALID_INPUT", "tune 不能为空")
        works = [work for work in self.works if (work.get("tune_pattern") or "") == name]
        if not works:
            raise ApiError("NOT_FOUND", "词牌不在 Tier 1 语料内", {"tune": name})
        sentence_counts: Counter = Counter()
        ratios: list[float] = []
        poets: set = set()
        periods: Counter = Counter()
        for work in works:
            prosody = work.get("prosody") or {}
            lines = prosody.get("lines") or []
            sentence_counts[len(lines)] += 1
            ping = sum(line.get("ping_count", 0) for line in lines)
            ze = sum(line.get("ze_count", 0) for line in lines)
            if ping + ze:
                ratios.append(round(ze * 100.0 / (ping + ze), 1))
            poet = work.get("poet")
            if isinstance(poet, dict):
                poets.add(poet.get("name") or "")
            periods[work.get("period_label") or "未标注"] += 1
        ratios.sort()
        median = (
            ratios[len(ratios) // 2]
            if ratios
            else None
        )
        return {
            "tune": name,
            "works": len(works),
            "poets": len(poets),
            "sentence_counts": [
                {"sentences": count, "works": works_count}
                for count, works_count in sorted(sentence_counts.items())
            ],
            "ze_ratio_median": median,
            "periods": dict(periods),
            "tier_label": "Tier 1 官方标注（逐句计数）",
            "boundary": (
                "同一词牌下可能有多种句式（体式），句数分布如实列出；"
                "仄比中位数只是总体水平，不代表任何具体作品。"
            ),
        }

    def summary(self) -> dict:
        self.load()
        periods: dict[str, int] = {}
        tunes = set()
        poets = set()
        for work in self.works:
            period = work.get("period_label") or "未标注"
            periods[period] = periods.get(period, 0) + 1
            if work.get("tune_pattern"):
                tunes.add(work["tune_pattern"])
            name = _poet_name(work)
            if name:
                poets.add(name)
        return {
            "works": len(self.works),
            "periods": periods,
            "poets": len(poets),
            "tunes": len(tunes),
            "source": "Tier 1 官方快照",
            "snapshot": str(self.snapshot_path.name),
        }

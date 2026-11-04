"""统一作品库：Tier 1 官方快照 + Tier 2 同源语料（清词 + 宋词）。

为什么要这一层：
- **Tier 1** 只有 423 篇，但它是**唯一官方基准口径**（逐句平仄计数以它为准）；
- **Tier 2** 有 43,191 首同源语料（清词 26,742 + 宋词 16,449），含逐句带调拼音与韵位，
  是「读词」与「风格」的真正语料底子。

两者口径不同、**绝不能混算**（V6.1 ADR-0002），但**浏览与检索应该都看得到**——
界面上只挂 Tier 1，会让人误以为整个知识库只有 400 多首，那是不实实的。

ID 约定：Tier 1 用整数；Tier 2 用层级前缀字符串
（`t2a_` 同源清词 / `t2b_` 同源宋词；旧的 `t2_` 仍兼容，读作清词）。
"""

import json
import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path

from . import tier2
from . import sqlite_compat
from . import tune_norm
from .errors import ApiError

TIER2_PREFIX = "t2_"
TIER1_LABEL = "官方标注"
TIER2_LABEL = "同源清词（非官方口径）"
TIER2B_LABEL = "同源宋词（非官方口径）"
TIER_LABEL_BY_PREFIX = {
    "t2a_": TIER2_LABEL,
    "t2b_": TIER2B_LABEL,
    "t2_": TIER2_LABEL,
}

_CJK = re.compile(r"[㐀-䶿一-鿿]")
_LOCK = threading.Lock()
_COUNT_CACHE: dict = {}


def _cjk_count(text: str) -> int:
    return len(_CJK.findall(text or ""))


def _tune_filter(connection, tune: str) -> tuple:
    """按词牌筛选的 SQL 片段与参数。

    同源语料里同一调有两个名字的写法（望江南/忆江南）存在 `tune_aliases` 表，
    按别名筛时也要命中。但旧版索引库没有这张表——这时退回精确匹配，
    不让查询直接报错（宁可少命中，也不要整个检索挂掉）。
    """
    if not tune:
        return "", []
    try:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tune_aliases'"
        ).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        return "(tune = ? OR wid IN (SELECT wid FROM tune_aliases WHERE alias = ?))", [tune, tune]
    return "tune = ?", [tune]


def _poet_name(work: dict) -> str:
    poet = work.get("poet")
    if isinstance(poet, dict):
        return poet.get("name") or ""
    return work.get("poet_name") or (poet or "")


def _locator_path(settings) -> Path:
    return Path(settings.tier2_locator_path)


def tier2_available(settings) -> bool:
    path = _locator_path(settings)
    return path.is_file() and path.stat().st_size > 0


def _connect(settings) -> sqlite3.Connection:
    path = _locator_path(settings)
    if not path.is_file():
        raise ApiError(
            "KNOWLEDGE_UNAVAILABLE",
            "Tier 2 作品索引未就位",
            {"expected_path": str(path), "hint": "python app/scripts/build_tier2_locator.py"},
        )
    # 走兼容层：索引里有 FTS5(trigram) 表，系统 SQLite 过旧时打开就会失败。
    connection = sqlite_compat.connect("file:%s?mode=ro" % path.as_posix(), read_only=True)
    connection.row_factory = sqlite_compat.Row
    return connection


def tier2_counts(settings) -> dict:
    """Tier 2 的规模统计（按层分）。"""
    if not tier2_available(settings):
        return {"works": 0, "poets": 0, "tunes": 0, "available": False, "layers": []}
    with _LOCK:
        cached = _COUNT_CACHE.get("counts")
    if cached:
        return cached
    connection = _connect(settings)
    try:
        layers = [
            {
                "tier": row["tier"],
                "label": TIER2B_LABEL if row["tier"] == "Tier 2b" else TIER2_LABEL,
                "works": row["n"],
            }
            for row in connection.execute(
                "SELECT tier, count(*) AS n FROM works GROUP BY tier ORDER BY tier"
            )
        ]
        counts = {
            "works": connection.execute("SELECT count(*) FROM works").fetchone()[0],
            "poets": connection.execute("SELECT count(DISTINCT poet) FROM works").fetchone()[0],
            # 词牌数只算**规范词牌名**——题记体作品（作者只留题记、没给词牌）
            # 的调名是整句题记，不算词牌。
            # 判定走 `tune_norm.is_caption_like`（Python 侧），与词调身份层、
            # 作者候选用的是**同一套规则**——口径分散过一次（SQL 过滤更窄，
            # 报 1,898 而词调身份层报 1,897），这里统一。
            "tunes": len(
                [
                    name
                    for (name,) in connection.execute("SELECT DISTINCT tune FROM works")
                    if name and not tune_norm.is_caption_like(name)
                ]
            ),
            "available": True,
            "layers": layers,
        }
    finally:
        connection.close()
    with _LOCK:
        _COUNT_CACHE["counts"] = counts
    return counts


def is_tier2_id(work_id) -> bool:
    return isinstance(work_id, str) and work_id.startswith("t2")


def tier_of_id(work_id) -> str:
    """按 id 前缀判层级。"""
    if not isinstance(work_id, str):
        return "Tier 1"
    for prefix, label in TIER_LABEL_BY_PREFIX.items():
        if work_id.startswith(prefix):
            return "Tier 2b" if prefix == "t2b_" else "Tier 2a"
    return "Tier 1"


def _row_to_item(row) -> dict:
    lines = json.loads(row["lines"] or "[]")
    tier = row["tier"] if "tier" in row.keys() else "Tier 2a"
    return {
        "id": row["id"],
        "title": row["title"],
        "tune": row["tune"],
        "period": row["dynasty"],
        "poet": row["poet"],
        "lines": len(lines),
        "tier_label": TIER2B_LABEL if tier == "Tier 2b" else TIER2_LABEL,
        "source": tier,
        "ze_ratio": row["ze_ratio"],
        # 与检索模式（search_tier2）保持同一套字段：
        # 浏览模式没有正文命中句，但字段必须在，
        # 否则前端按 matched.map(...) 渲染时会炸
        "matched": [],
        "hits": 0,
    }


def list_tier2(settings, period: str = "", tune: str = "", poet: str = "", query: str = "",
               page: int = 1, page_size: int = 20, tier: str = "",
               offset: int | None = None) -> dict:
    """Tier 2 作品列表。

    `offset` 给了就用它（跨层分页要按**全局位置**取同源层的一段，
    用 page/page_size 算不出任意偏移）；不给则按 page 推算。

    过滤在 SQL 层做（title/poet/tune/dynasty/tier 有索引），正文命中用 LIKE 扫 `lines`——
    列表页本来就要展示作品，命中句在详情页给。
    """
    where = []
    params: list = []
    connection = _connect(settings)
    try:
        if period:
            where.append("dynasty = ?")
            params.append(period)
        if tune:
            fragment, tune_params = _tune_filter(connection, tune)
            where.append(fragment)
            params.extend(tune_params)
        if poet:
            where.append("poet = ?")
            params.append(poet)
        if tier in ("2a", "2b"):
            where.append("tier = ?")
            params.append("Tier 2" + tier[-1])
        if query:
            where.append("(title LIKE ? OR poet LIKE ? OR lines LIKE ?)")
            like = "%" + query + "%"
            params.extend([like, like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = connection.execute("SELECT count(*) FROM works" + clause, params).fetchone()[0]
        row_offset = offset if offset is not None else (page - 1) * page_size
        rows = connection.execute(
            "SELECT id, title, poet, dynasty, tune, lines, ze_ratio, tier FROM works"
            + clause
            + " ORDER BY poet, title LIMIT ? OFFSET ?",
            params + [page_size, row_offset],
        ).fetchall()
    finally:
        connection.close()
    return {
        "items": [_row_to_item(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_tier2(settings, work_id: str) -> dict:
    """取一篇 Tier 2 作品（含逐句与逐句拼音）。"""
    connection = _connect(settings)
    try:
        row = connection.execute(
            "SELECT id, title, poet, dynasty, tune, first_line, lines, pinyin_lines, "
            "ping, ze, ze_ratio, line_count, rhyme_char, tier FROM works WHERE id = ?",
            (work_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ApiError("NOT_FOUND", "作品不存在或未发布", {"work_id": work_id})
    lines = json.loads(row["lines"] or "[]")
    tier = row["tier"]
    return {
        "id": row["id"],
        "title": row["title"],
        "tune": row["tune"],
        "period": row["dynasty"],
        "poet": row["poet"],
        "text": "\n".join(lines),
        "first_line": row["first_line"],
        "split_after_line": None,
        "tier_label": TIER2B_LABEL if tier == "Tier 2b" else TIER2_LABEL,
        "source": tier,
        "warning": tier2.WARNING,
        "lines": lines,
        "pinyin_lines": json.loads(row["pinyin_lines"] or "[]"),
        "ping": row["ping"],
        "ze": row["ze"],
        "ze_ratio": row["ze_ratio"],
        "line_count": row["line_count"],
        "rhyme_char": row["rhyme_char"],
    }


def _matches_from_postings(connection, gram: str, n: int) -> dict[int, list[int]]:
    """查倒排：`wid → [line_no, …]`。"""
    row = connection.execute(
        "SELECT ids FROM postings WHERE gram = ? AND n = ?", (gram, n)
    ).fetchone()
    if not row:
        return {}
    out: dict[int, list[int]] = {}
    for item in row["ids"].split(","):
        wid_text, _, line_text = item.partition(":")
        out.setdefault(int(wid_text), []).append(int(line_text))
    return out


def _matches_from_fts(connection, query: str, limit: int) -> dict[int, list[int]]:
    """FTS5 trigram 路径：拿到 wid 集合后回表定位命中句。"""
    try:
        wids = [
            row[0]
            for row in connection.execute(
                "SELECT rowid FROM works_fts WHERE works_fts MATCH ? LIMIT ?", (query, limit * 40)
            )
        ]
    except sqlite3.Error:
        return {}
    out: dict[int, list[int]] = {}
    for wid in wids:
        row = connection.execute("SELECT lines FROM works WHERE wid = ?", (wid,)).fetchone()
        if not row:
            continue
        hits = [
            index + 1
            for index, text in enumerate(json.loads(row["lines"] or "[]"))
            if query in text
        ]
        if hits:
            out[wid] = hits
    return out


def search_tier2(settings, query: str, period: str = "", tune: str = "", poet: str = "",
                 limit: int = 20, tier: str = "") -> dict:
    """Tier 2 检索。

    1–2 字走紧凑倒排（`postings`），≥3 字走 FTS5 trigram；两者都在索引里，
    不做全表 LIKE 扫。目标机 SQLite 没有 trigram 分词器时自动退回 LIKE，
    功能不变、只是慢一点（并如实说明用的是哪种模式）。
    """
    n = len(query)
    connection = _connect(settings)
    try:
        if n >= 3:
            matches = _matches_from_fts(connection, query, limit)
            mode = "trigram"
            if not matches:
                matches = _matches_from_fts_like(connection, query, period, tune, poet, tier)
                mode = "substring"
        else:
            matches = _matches_from_postings(connection, query, n)
            mode = "bigram" if n == 2 else "unigram"
            if not matches:
                matches = _matches_from_fts_like(connection, query, period, tune, poet, tier)
                mode = "substring"

        where = []
        params: list = []
        if period:
            where.append("dynasty = ?")
            params.append(period)
        if tune:
            fragment, tune_params = _tune_filter(connection, tune)
            where.append(fragment)
            params.extend(tune_params)
        if poet:
            where.append("poet LIKE ?")
            params.append("%" + poet + "%")
        if tier in ("2a", "2b"):
            where.append("tier = ?")
            params.append("Tier 2" + tier[-1])
        filter_and = (" AND " + " AND ".join(where)) if where else ""

        # 候选作品数（用于 total）：按 wid 分批查，避免 SQLite 的 IN 参数上限
        candidates = sorted(matches.keys())
        rows_meta = []
        for start in range(0, len(candidates), 900):
            chunk = candidates[start : start + 900]
            placeholders = ",".join("?" * len(chunk))
            sql = (
                "SELECT wid, id, title, poet, dynasty, tune, ze_ratio, tier "
                "FROM works WHERE wid IN (%s)%s" % (placeholders, filter_and)
            )
            rows_meta.extend(connection.execute(sql, chunk + params).fetchall())

        # 正文只取要展示的少数几篇——大结果集不必整篇读进内存
        wanted = rows_meta[: max(limit * 3, 30)]
        items = []
        for meta in wanted:
            row = connection.execute(
                "SELECT lines FROM works WHERE wid = ?", (meta["wid"],)
            ).fetchone()
            if row is None:
                continue
            lines = json.loads(row["lines"] or "[]")
            line_nos = matches.get(meta["wid"], [])
            matched = [
                {"line_no": line_no, "text": lines[line_no - 1]}
                for line_no in line_nos
                if 1 <= line_no <= len(lines)
            ]
            items.append(
                {
                    "id": meta["id"],
                    "title": meta["title"],
                    "tune": meta["tune"],
                    "period": meta["dynasty"],
                    "poet": meta["poet"],
                    "matched": matched[:3],
                    "hits": len(matched),
                    "tier_label": TIER2B_LABEL if meta["tier"] == "Tier 2b" else TIER2_LABEL,
                    "source": meta["tier"],
                }
            )
        items.sort(key=lambda entry: (-entry["hits"], entry["id"]))
        return {
            "q": query,
            "mode": mode,
            "items": items[:limit],
            "total": len(rows_meta),
            "index": "sqlite",
            "tier": tier or "all",
            "boundary": "Tier 2 为同源公开整理本，与 Tier 1 官方标注口径不同，结果不混算",
        }
    finally:
        connection.close()


def _matches_from_fts_like(connection, query: str, period: str, tune: str, poet: str,
                            tier: str) -> dict[int, list[int]]:
    """没有倒排 / trigram 时的兜底：全表 LIKE。慢，但功能不缺。"""
    where = ["lines LIKE ?"]
    params: list = ["%" + query + "%"]
    if period:
        where.append("dynasty = ?")
        params.append(period)
    if tune:
        fragment, tune_params = _tune_filter(connection, tune)
        where.append(fragment)
        params.extend(tune_params)
    if poet:
        where.append("poet LIKE ?")
        params.append("%" + poet + "%")
    if tier in ("2a", "2b"):
        where.append("tier = ?")
        params.append("Tier 2" + tier[-1])
    clause = " WHERE " + " AND ".join(where)
    out: dict[int, list[int]] = {}
    for row in connection.execute(
        "SELECT wid, lines FROM works" + clause + " LIMIT 2000", params
    ):
        hits = [
            index + 1
            for index, text in enumerate(json.loads(row["lines"] or "[]"))
            if query in text
        ]
        if hits:
            out[row["wid"]] = hits
    return out


def tune_stats(settings, tune: str, tier: str = "all") -> dict:
    """同调统计：按词牌给作品数、句数分布与仄比中位数。

    Tier 1 用官方逐句计数；Tier 2 用同源带调拼音统计的逐句平仄——
    两层口径不同，响应里 `tier_label` 写明来源，**不混算**。
    """
    name = (tune or "").strip()
    if not name:
        raise ApiError("INVALID_INPUT", "tune 不能为空")
    if tier in ("1", "all"):
        official = _tier1_tune_stats(settings, name)
        if official is not None:
            official["tier"] = "1"
            return official
    if tier in ("2a", "2b", "all"):
        if not tier2_available(settings):
            if tier == "all":
                raise ApiError("NOT_FOUND", "词牌不在可用语料内", {"tune": name})
            raise ApiError("KNOWLEDGE_UNAVAILABLE", "Tier 2 索引未就位")
        result = _tier2_tune_stats(settings, name, tier)
        if result is not None:
            return result
    raise ApiError("NOT_FOUND", "词牌不在可用语料内", {"tune": name, "tier": tier})


def _tier1_tune_stats(settings, name: str) -> dict | None:
    """Tier 1 的同调统计（官方逐句计数）。"""
    from .store import Store  # 延迟导入避免循环依赖

    store = Store(settings.snapshot_path, settings.index_path)
    try:
        store.load()
    except ApiError:
        return None
    works = [work for work in store.all() if (work.get("tune_pattern") or "") == name]
    if not works:
        return None
    sentence_counts: Counter = Counter()
    ratios: list[float] = []
    poets: set = set()
    periods: Counter = Counter()
    for work in works:
        lines = (work.get("prosody") or {}).get("lines") or []
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
    return {
        "tune": name,
        "works": len(works),
        "poets": len(poets),
        "sentence_counts": [
            {"sentences": count, "works": item} for count, item in sorted(sentence_counts.items())
        ],
        "ze_ratio_median": ratios[len(ratios) // 2] if ratios else None,
        "periods": dict(periods),
        "tier_label": "Tier 1 " + TIER1_LABEL + "（逐句计数）",
        "boundary": "同一词牌下可能有多种句式（体式），句数分布如实列出；仄比中位数只是总体水平，不代表任何具体作品。",
    }


def _tier2_tune_stats(settings, name: str, tier: str) -> dict | None:
    """Tier 2 的同调统计（同源带调拼音口径）。"""
    connection = _connect(settings)
    try:
        where = []
        params: list = []
        fragment, tune_params = _tune_filter(connection, name)
        where.append(fragment)
        params.extend(tune_params)
        if tier in ("2a", "2b"):
            where.append("tier = ?")
            params.append("Tier 2" + tier[-1])
        clause = " WHERE " + " AND ".join(where)
        rows = connection.execute(
            "SELECT line_count, ze_ratio, poet, dynasty FROM works" + clause, params
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        return None
    sentence_counts: Counter = Counter()
    ratios: list[float] = []
    poets: set = set()
    periods: Counter = Counter()
    for row in rows:
        sentence_counts[int(row["line_count"] or 0)] += 1
        if row["ze_ratio"] is not None:
            ratios.append(float(row["ze_ratio"]))
        if row["poet"]:
            poets.add(row["poet"])
        periods[row["dynasty"] or "未标注"] += 1
    ratios.sort()
    return {
        "tune": name,
        "works": len(rows),
        "poets": len(poets),
        "sentence_counts": [
            {"sentences": count, "works": item} for count, item in sorted(sentence_counts.items())
        ],
        "ze_ratio_median": ratios[len(ratios) // 2] if ratios else None,
        "periods": dict(periods),
        "tier_label": "同源带调拼音（非官方口径）",
        "boundary": "同源层逐句平仄由带调拼音统计，与官方逐句计数口径不同；句数分布如实列出，仄比中位数只是总体水平。",
    }


def authors(settings, keyword: str = "", limit: int = 50, tier: str = "") -> dict:
    """作者候选：姓名 + 作品数 + 词牌数，供检索页的「按作者检索」用。

    官方层与同源层合并统计（同源层的作品数远大于官方层，所以分开列更清楚）。
    """
    key = (keyword or "").strip()
    out: list[dict] = []

    # 官方层
    from .store import Store

    store = Store(settings.snapshot_path, settings.index_path)
    store.load()
    official: dict[str, dict] = {}
    for work in store.all():
        name = _poet_name(work)
        if not name or (key and key not in name):
            continue
        entry = official.setdefault(
            name, {"name": name, "works": 0, "tunes": set(), "periods": {}}
        )
        entry["works"] += 1
        entry["tunes"].add(work.get("tune_pattern") or "")
        period = work.get("period_label") or "未标注"
        entry["periods"][period] = entry["periods"].get(period, 0) + 1
    for entry in official.values():
        out.append(
            {
                "name": entry["name"],
                "official_works": entry["works"],
                "corpus_works": 0,
                "tunes": len([t for t in entry["tunes"] if t]),
                "periods": entry["periods"],
            }
        )

    # 同源层
    if tier2_available(settings) and tier in ("", "2a", "2b", "all"):
        connection = _connect(settings)
        try:
            where = []
            params: list = []
            if key:
                where.append("poet LIKE ?")
                params.append("%" + key + "%")
            if tier in ("2a", "2b"):
                where.append("tier = ?")
                params.append("Tier 2" + tier[-1])
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            rows = connection.execute(
                "SELECT poet, tune, dynasty, tier FROM works" + clause, params
            ).fetchall()
        finally:
            connection.close()
        corpus_counts: dict[str, dict] = {}
        for row in rows:
            name = row["poet"] or ""
            if not name:
                continue
            entry = corpus_counts.setdefault(
                name, {"official_works": 0, "corpus_works": 0, "tunes": set(), "periods": {}}
            )
            entry["corpus_works"] += 1
            # 题记体作品（作者只留题记、没给词牌）的调名是整句题记，不算词牌
            tune_name = (row["tune"] or "").strip()
            if tune_name and not tune_norm.is_caption_like(tune_name):
                entry["tunes"].add(tune_name)
            period = row["dynasty"] or "未标注"
            entry["periods"][period] = entry["periods"].get(period, 0) + 1
        existing = {item["name"]: item for item in out}
        for name, entry in corpus_counts.items():
            if name in existing:
                existing[name]["corpus_works"] = entry["corpus_works"]
                existing[name]["tunes"] = max(existing[name]["tunes"], len([t for t in entry["tunes"] if t]))
                for period, count in entry["periods"].items():
                    existing[name]["periods"][period] = (
                        existing[name]["periods"].get(period, 0) + count
                    )
            else:
                out.append(
                    {
                        "name": name,
                        "official_works": 0,
                        "corpus_works": entry["corpus_works"],
                        "tunes": len([t for t in entry["tunes"] if t]),
                        "periods": entry["periods"],
                    }
                )

    out.sort(key=lambda item: -(item["official_works"] + item["corpus_works"]))
    return {
        "items": out[: max(1, min(limit, 200))],
        "total": len(out),
        "keyword": key,
    }


def tier2_line_stats(work: dict) -> list[dict]:
    """Tier 2 逐句字数与平仄计数（**同源拼音口径，非官方**）。

    口径与官方 API 对齐：**只有有效字音标注的字参与统计**（官方文档：
    「仅有有效字音标注的句子参与统计，标点和空白不计」）。所以
    `character_count` = 参与统计的字数 = ping + ze；轻声等没有调号的字记在
    `unknown_count` 里，不混进计数。

    这样同源层的逐句数据能通过引擎的**声律不变量校验**
    （ping + ze == character_count），与官方层同构。
    """
    lines = work.get("lines") or []
    pinyin_lines = work.get("pinyin_lines") or []
    out = []
    for index, text in enumerate(lines):
        total_chars = _cjk_count(text)
        tones = tier2.tones_of(pinyin_lines[index]) if index < len(pinyin_lines) else []
        ping = sum(1 for tone in tones if tone in (1, 2))
        ze = sum(1 for tone in tones if tone in (3, 4))
        counted = ping + ze
        out.append(
            {
                "line_no": index + 1,
                "text": text,
                "character_count": counted,
                "ping_count": ping,
                "ze_count": ze,
                "unknown_count": max(0, total_chars - counted),
                "total_chars": total_chars,
            }
        )
    return out


def load_work(settings, work_id) -> dict:
    """统一取作品：**与 Tier 1 同构**（含 `prosody.lines`）。

    Tier 1 是整数 id；Tier 2 是 `t2a_` / `t2b_` 前缀的字符串 id。
    返回的作品对象字段与官方快照一致，因此事实卡、证据链、引擎逻辑
    **不需要为同源层再写一套**。
    """
    if is_tier2_id(work_id):
        work = get_tier2(settings, work_id)
        stats = tier2_line_stats(work)
        lines = [
            {
                "line_no": item["line_no"],
                "text": item["text"],
                "character_count": item["character_count"],
                "ping_count": item["ping_count"],
                "ze_count": item["ze_count"],
            }
            for item in stats
        ]
        return {
            "id": work["id"],
            "title": work["title"],
            "tune_pattern": work["tune"],
            "period_label": work["period"],
            "text": work["text"],
            "poet": {"name": work["poet"], "dynasty": work["period"]},
            "prosody": {
                # 分段公式与官方口径一致（官方 API 文档：第 max(1, floor(句数/2)) 句之后分段）
                "split_after_line": max(1, len(lines) // 2),
                "lines": lines,
            },
            "_source": "同源带调拼音（非官方口径）",
            "_tier_label": TIER2B_LABEL if work["source"] == "Tier 2b" else TIER2_LABEL,
        }
    from .store import Store  # 延迟导入避免循环依赖

    store = Store(settings.snapshot_path, settings.index_path)
    try:
        return store.get(int(work_id))
    except (TypeError, ValueError):
        raise ApiError(
            "INVALID_INPUT",
            "作品编号格式不对",
            {"work_id": work_id, "hint": "官方层用整数 id，同源层用 t2a_ / t2b_ 前缀"},
        )

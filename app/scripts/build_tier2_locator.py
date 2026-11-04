#!/usr/bin/env python3
"""构建 Tier 2 作品库索引：清词 + 宋词，带紧凑倒排与 FTS5。

比原 `build_tier2_locator.py` 多的三件事：

1. **宋词也进来**。poetry-source 里本来就有 16,449 首宋词（此前只索引了清词 26,742 首），
   现在清 + 宋合计 **43,191 首**，这才是「读词」的语料底子。
   倒排（`postings`）与 FTS5 trigram 随之覆盖全部 43,191 首——检索不再全表 LIKE。
2. **紧凑倒排**。原来检索靠 SQLite LIKE 全表扫（26,742 首 40–90 ms，越大越慢）。
   现在建 `postings(gram, n, ids)`——每个 gram 一行、postings 打包成一个字符串，
   1–2 字查询直接命中倒排；≥3 字走 FTS5 trigram。
3. **id 分层前缀**。`t2a_` 清词 / `t2b_` 宋词，一眼能看出层级（旧的 `t2_` 仍兼容读作清词）。

**口径**：这一层全部是「非官方口径」，逐句平仄由同源带调拼音统计，
不能当官方标注用（V6.1 §1.7 / ADR-0002）。原始语料只在构建期使用、不随交付物分发。

用法：
    python app/scripts/build_tier2_locator.py [--out PATH] [--no-index]
退出码：0 成功；1 失败；2 源目录缺失。
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
REFERENCE = REPO / ".workbuddy" / "reference" / "tier2"
OUT = APP / "data" / "tier2_locator.sqlite3"

# 词牌归一与运行时共用一份逻辑（core/tune_norm.py），改一处两边同步
BACKEND = APP / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import tune_norm  # noqa: E402

# 层级目录 → (层级标签, id 前缀)
# 清词与宋词同源（poetry-source），带调拼音；合起来 43,191 首。
SOURCES = {
    "qing": ("Tier 2a", "t2a_"),
    "song": ("Tier 2b", "t2b_"),
}

TONE_MARKS = {
    "āēīōūǖ": 1, "áéíóúǘ": 2, "ǎěǐǒǔǚ": 3, "àèìòùǜ": 4,
}


def tones_of(pinyin_text: str) -> list[int]:
    """从带调字母串里按顺序取每个音节的调号。

    同源拼音的标点与前后音节是**粘在一起**的（"fùjì" = 妇+记），
    空格切分对不齐，只能靠调号字符一音节一个来数。
    """
    tones = []
    for char in pinyin_text or "":
        for marks, tone in TONE_MARKS.items():
            if char in marks:
                tones.append(tone)
                break
    return tones


def cjk_only(text: str) -> str:
    return "".join(ch for ch in (text or "") if "一" <= ch <= "鿿")


def cjk_count(text: str) -> int:
    return len(cjk_only(text))


def read_source(folder: Path, tier_label: str, prefix: str):
    """读一个语料层，返回作品字典列表与跳过数。"""
    files = sorted(folder.glob("*.pinyin.json"))
    rows = []
    skipped = 0
    for path in files:
        for record in json.loads(path.read_text(encoding="utf-8")):
            content = record.get("content") or []
            pinyin = record.get("pinyin") or []
            if not content or len(content) != len(pinyin):
                skipped += 1
                continue
            ping = ze = 0
            per_line = []
            for text, pinyin_text in zip(content, pinyin):
                tones = tones_of(pinyin_text)
                line_ping = sum(1 for tone in tones if tone in (1, 2))
                line_ze = sum(1 for tone in tones if tone in (3, 4))
                ping += line_ping
                ze += line_ze
                per_line.append([line_ping, line_ze])
            total = ping + ze
            if total == 0:
                skipped += 1
                continue
            title = record.get("title") or ""
            tune = title.split("·", 1)[0] if "·" in title else title
            first_line = content[0]
            rows.append(
                {
                    "id": prefix + (record.get("id") or title),
                    "title": title,
                    "poet": record.get("authorName") or "",
                    "dynasty": record.get("dynasty") or "",
                    "tune": tune,
                    "first_line": first_line,
                    "first_key": cjk_only(first_line)[:8],
                    "lines": list(content),
                    "pinyin_lines": list(pinyin),
                    "ping": ping,
                    "ze": ze,
                    "ze_ratio": round(ze * 100.0 / total, 1),
                    "line_count": len(per_line),
                    # 句长序列（每句字数）——体式（form）的判定依据，
                    # 存成字符串是为了不必在运行时解析整段 JSON
                    "pattern": ",".join(str(cjk_count(line)) for line in content),
                    "rhyme_char": cjk_only(first_line)[-1:] if cjk_only(first_line) else "",
                    "tier": tier_label,
                }
            )
    return rows, skipped


def build_index(connection: sqlite3.Connection, works: list[dict]) -> dict:
    """建紧凑倒排 + FTS5 trigram。

    倒排：`postings(gram, n, ids)`，`ids` 是 `wid:line_no,wid:line_no,…` 的紧凑串。
    命中句直接在 postings 里，不需要再回表 LIKE 一遍。
    """
    connection.execute(
        """
        CREATE TABLE postings(
            gram TEXT NOT NULL,
            n INTEGER NOT NULL,
            ids TEXT NOT NULL,
            PRIMARY KEY(gram, n)
        ) WITHOUT ROWID
        """
    )
    print("  建倒排……", flush=True)
    started = time.time()
    buckets: dict[tuple[str, int], list[str]] = {}
    for wid, work in enumerate(works, start=1):
        for line_no, text in enumerate(work["lines"], start=1):
            chars = cjk_only(text)
            for index, char in enumerate(chars):
                buckets.setdefault((char, 1), []).append("%d:%d" % (wid, line_no))
                if index + 1 < len(chars):
                    buckets.setdefault((chars[index : index + 2], 2), []).append("%d:%d" % (wid, line_no))
    rows = [(gram, n, ",".join(ids)) for (gram, n), ids in buckets.items()]
    connection.executemany("INSERT INTO postings VALUES (?,?,?)", rows)
    connection.commit()
    unigram = sum(1 for (_g, n) in buckets if n == 1)
    bigram = sum(1 for (_g, n) in buckets if n == 2)
    postings_count = sum(len(ids) for ids in buckets.values())
    print(
        "  倒排完成：%d 个 gram（一元 %d / 二元 %d），%d 条 postings，%.1f 秒"
        % (len(rows), unigram, bigram, postings_count, time.time() - started),
        flush=True,
    )

    print("  建 FTS5 trigram……", flush=True)
    started = time.time()
    try:
        connection.execute("CREATE VIRTUAL TABLE works_fts USING fts5(text, tokenize='trigram')")
        connection.executemany(
            "INSERT INTO works_fts(rowid, text) VALUES (?, ?)",
            [(wid, " ".join(work["lines"])) for wid, work in enumerate(works, start=1)],
        )
        connection.commit()
        fts = "可用"
    except sqlite3.Error as error:
        connection.rollback()
        fts = "不可用（%s）" % error
    print("  FTS5 完成：%s，%.1f 秒" % (fts, time.time() - started), flush=True)
    return {"grams": len(rows), "postings": postings_count, "fts": fts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--no-index", action="store_true", help="只建作品表，不建倒排与 FTS5")
    args = parser.parse_args()
    out = Path(args.out)

    if not REFERENCE.is_dir():
        print("缺少同源语料目录：%s" % REFERENCE, file=sys.stderr)
        return 2

    all_rows: list[dict] = []
    for folder_name, (tier_label, prefix) in SOURCES.items():
        folder = REFERENCE / folder_name
        if not folder.is_dir():
            print("  跳过 %s（目录不存在）" % folder_name)
            continue
        rows, skipped = read_source(folder, tier_label, prefix)
        print("  %s：%d 首（跳过 %d 首：音节不足或无拼音）" % (tier_label, len(rows), skipped))
        all_rows.extend(rows)

    if not all_rows:
        print("没有可用的语料", file=sys.stderr)
        return 2

    # 词牌归一：把「壶中天题公曼仇史，即以为赠」这类「词牌+题注」切回规范词牌。
    # 切不出来的保留原值——宁可不准，也不丢信息；原题注始终留在 title 里。
    pairs = [(index, work["title"], work["tune"]) for index, work in enumerate(all_rows)]
    norm = tune_norm.normalize(pairs)
    for index, work in enumerate(all_rows):
        work["tune"] = norm["canonical"][index]
    alias_rows = [(index + 1, alias) for index, alias in norm["aliases"]]
    stats = norm["stats"]
    print(
        "  词牌归一：切出 %d 首，冷门词牌 %d 首，题注体 %d 首；distinct %d → %d"
        % (stats["cut"], stats["cold"], stats["kept"], stats["distinct_before"], stats["distinct_after"])
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    connection = sqlite3.connect(out)
    try:
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute(
            """
            CREATE TABLE works(
                wid INTEGER PRIMARY KEY,
                id TEXT UNIQUE,
                title TEXT,
                poet TEXT,
                dynasty TEXT,
                tune TEXT,
                first_line TEXT,
                first_key TEXT,
                lines TEXT,
                pinyin_lines TEXT,
                ping INTEGER,
                ze INTEGER,
                ze_ratio REAL,
                line_count INTEGER,
                rhyme_char TEXT,
                tier TEXT,
                pattern TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO works(id,title,poet,dynasty,tune,first_line,first_key,lines,pinyin_lines,"
            "ping,ze,ze_ratio,line_count,rhyme_char,tier,pattern) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    work["id"], work["title"], work["poet"], work["dynasty"], work["tune"],
                    work["first_line"], work["first_key"],
                    json.dumps(work["lines"], ensure_ascii=False),
                    json.dumps(work["pinyin_lines"], ensure_ascii=False),
                    work["ping"], work["ze"], work["ze_ratio"],
                    work["line_count"], work["rhyme_char"], work["tier"], work["pattern"],
                )
                for work in all_rows
            ],
        )
        connection.execute("CREATE INDEX idx_first ON works(first_key)")
        connection.execute("CREATE INDEX idx_poet ON works(poet)")
        connection.execute("CREATE INDEX idx_tune ON works(tune)")
        connection.execute("CREATE INDEX idx_tier ON works(tier)")
        # 斜杠并列写法的另一个名字（望江南/忆江南）：按别名筛词牌时用得上
        connection.execute("CREATE TABLE tune_aliases(wid INTEGER, alias TEXT)")
        connection.executemany(
            "INSERT INTO tune_aliases(wid, alias) VALUES (?, ?)", alias_rows
        )
        connection.execute("CREATE INDEX idx_tune_alias ON tune_aliases(alias)")
        connection.commit()

        if not args.no_index:
            build_index(connection, all_rows)
    finally:
        connection.close()

    print("生成 %s：%d 首" % (out, len(all_rows)))
    print("  体积 %.1f MB · 构建于 %s" % (out.stat().st_size / 1024 / 1024, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

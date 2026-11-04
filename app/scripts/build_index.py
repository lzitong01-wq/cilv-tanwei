#!/usr/bin/env python3
"""构建检索索引：FTS5(trigram) + 一元/二元倒排。

为什么需要两套：
- FTS5 的 trigram 分词器要求查询串 ≥3 字符，中文二字词（闺怨、边塞）永远不命中；
- 故对 1–2 字的查询改走自建 grams 倒排表，≥3 字走 trigram。

用法：python app/scripts/build_index.py [--snapshot PATH] [--out PATH]
退出码：0 成功；1 快照缺失；2 参数错误。
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = APP_ROOT.parent / "data" / "知识库审计快照V1.jsonl"
DEFAULT_OUT = APP_ROOT / "data" / "index.sqlite3"

CJK_START, CJK_END = "\u3400", "\u9fff"


def cjk_only(text: str) -> str:
    return "".join(ch for ch in (text or "") if CJK_START <= ch <= CJK_END)


def poet_of(work: dict) -> str:
    poet = work.get("poet")
    if isinstance(poet, dict):
        return poet.get("name") or ""
    return work.get("poet_name") or ""


def build(snapshot: Path, out: Path) -> dict:
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    rows = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines() if line.strip()]

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    connection = sqlite3.connect(out)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE works(
                id INTEGER PRIMARY KEY,
                title TEXT, tune TEXT, period TEXT, poet TEXT,
                text TEXT, line_count INTEGER
            );
            CREATE TABLE lines(
                work_id INTEGER, line_no INTEGER, text TEXT,
                character_count INTEGER, ping_count INTEGER, ze_count INTEGER
            );
            CREATE TABLE grams(
                gram TEXT, n INTEGER, work_id INTEGER, line_no INTEGER
            );
            """
        )

        work_rows = []
        line_rows = []
        gram_rows = []
        for work in rows:
            work_id = int(work["id"])
            prosody = work.get("prosody") or {}
            lines = sorted(prosody.get("lines") or [], key=lambda line: line["line_no"])
            work_rows.append(
                (
                    work_id,
                    work.get("title") or "",
                    work.get("tune_pattern") or "",
                    work.get("period_label") or "",
                    poet_of(work),
                    work.get("text") or "",
                    len(lines),
                )
            )
            for line in lines:
                text = line.get("text") or ""
                line_rows.append(
                    (
                        work_id,
                        line["line_no"],
                        text,
                        line["character_count"],
                        line["ping_count"],
                        line["ze_count"],
                    )
                )
                chars = cjk_only(text)
                for index, char in enumerate(chars):
                    gram_rows.append((char, 1, work_id, line["line_no"]))
                    if index + 1 < len(chars):
                        gram_rows.append((chars[index : index + 2], 2, work_id, line["line_no"]))

        connection.executemany("INSERT INTO works VALUES (?,?,?,?,?,?,?)", work_rows)
        connection.executemany("INSERT INTO lines VALUES (?,?,?,?,?,?)", line_rows)
        connection.executemany("INSERT INTO grams VALUES (?,?,?,?)", gram_rows)
        connection.execute("CREATE INDEX idx_grams ON grams(gram, n)")
        connection.execute("CREATE INDEX idx_lines_work ON lines(work_id, line_no)")
        connection.execute(
            "CREATE VIRTUAL TABLE works_fts USING fts5("
            "text, content='works', content_rowid='id', tokenize='trigram')"
        )
        connection.execute("INSERT INTO works_fts(works_fts) VALUES('rebuild')")
        connection.commit()

        stats = {
            "works": len(work_rows),
            "lines": len(line_rows),
            "grams": len(gram_rows),
            "unigrams": sum(1 for row in gram_rows if row[1] == 1),
            "bigrams": sum(1 for row in gram_rows if row[1] == 2),
        }
    finally:
        connection.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        stats = build(Path(args.snapshot), Path(args.out))
    except FileNotFoundError as exc:
        print("快照不存在：%s" % exc, file=sys.stderr)
        return 1
    cost = time.perf_counter() - started
    size = Path(args.out).stat().st_size / 1024 / 1024
    print("索引已生成：%s" % args.out)
    print("  作品 %d · 句 %d · 一元 %d · 二元 %d · %.1f MB · %.2fs"
          % (stats["works"], stats["lines"], stats["unigrams"], stats["bigrams"], size, cost))
    return 0


if __name__ == "__main__":
    sys.exit(main())

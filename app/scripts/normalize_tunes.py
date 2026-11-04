#!/usr/bin/env python3
"""就地归一同源语料的词牌字段。

**为什么需要**：同源语料标题多为「词牌 题注」形态，早期构建只按「·」切一刀，
于是 43,191 首里有 2,675 个「词牌」其实是题注串——按词牌筛选永远筛不出来，
词牌候选列表混着几千个伪词牌。

**做什么**：用 `core/tune_norm.py` 的自举词表把规范词牌切回来。

- `works.tune` 更新为规范词牌（**原题注一字不丢，仍在 `works.title` 里**）
- 新建 `tune_aliases(wid, alias)` 表，存「望江南/忆江南」这类并列写法的另一个名字
- 倒排 `postings` 与 FTS5 `works_fts` **不重建**（它们索引的是正文，与 tune 无关）

用法：
    python app/scripts/normalize_tunes.py [--db app/data/tier2_locator.sqlite3] [--dry-run]

退出码：0 成功；1 失败。
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
BACKEND = APP / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import tune_norm  # noqa: E402

DEFAULT_DB = APP / "data" / "tier2_locator.sqlite3"


def raw_tune_of(title: str) -> str:
    """从标题取原始调名——与构建脚本 `build_tier2_locator.py` 的切法一致。"""
    text = title or ""
    return text.split("·", 1)[0] if "·" in text else text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print("找不到索引库：%s" % db_path, file=sys.stderr)
        print("请先运行 python app/scripts/build_tier2_locator.py", file=sys.stderr)
        return 1

    started = time.time()
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT wid, title, tune FROM works").fetchall()
        if not rows:
            print("索引库里没有作品，无法归一", file=sys.stderr)
            return 1

        # 原始调名一律**从 title 重新提取**，不读 tune 列——
        # tune 列可能已被上一次归一覆盖过，用它会导致不幂等
        # （斜杠并列的别名信息会丢）。
        pairs = [
            (row["wid"], row["title"], raw_tune_of(row["title"]))
            for row in rows
        ]
        result = tune_norm.normalize(pairs)
        stats = result["stats"]

        print("归一前 distinct 词牌：%d" % stats["distinct_before"])
        print("归一后 distinct 词牌：%d" % stats["distinct_after"])
        print("切出规范词牌：%d 首（%.1f%%）" % (stats["cut"], 100.0 * stats["cut"] / stats["total"]))
        print("冷门词牌原样保留：%d 首" % stats["cold"])
        print("题注体原样保留：%d 首" % stats["kept"])
        print("别名条数：%d" % len(result["aliases"]))

        if args.dry_run:
            print("--dry-run：未写库")
            return 0

        connection.execute("BEGIN")
        connection.executemany(
            "UPDATE works SET tune = ? WHERE wid = ?",
            [(tune, wid) for wid, tune in result["canonical"].items()],
        )
        connection.execute("DROP TABLE IF EXISTS tune_aliases")
        connection.execute(
            "CREATE TABLE tune_aliases(wid INTEGER, alias TEXT)"
        )
        connection.executemany(
            "INSERT INTO tune_aliases(wid, alias) VALUES (?, ?)",
            result["aliases"],
        )
        connection.execute("CREATE INDEX idx_tune_alias ON tune_aliases(alias)")
        connection.commit()
        connection.execute("ANALYZE")
        connection.commit()
    finally:
        connection.close()

    print("完成，用时 %.1f 秒" % (time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())

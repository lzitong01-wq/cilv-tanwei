#!/usr/bin/env python3
"""生成校订队列：比对库内官方计数与本地字典计算，产出可复核的差异条目。

原则（对应分册 B §B5 的 L0/L1）：
- 差异只表示"与官方不一致"，**不断言谁错**；
- 每条建议必须给出可核验的依据（差几个平、句内哪些多音字）；
- 无法解释的差异单独标记 unresolved，不硬给建议值；
- 幂等键取条目内容哈希，重复生成不会产生重复条目。

用法：python app/scripts/build_revisions.py [--snapshot PATH] [--out PATH] [--rebuild]
"""

import argparse
import datetime
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from pypinyin import Style, pinyin

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = APP_ROOT.parent / "data" / "知识库审计快照V1.jsonl"
DEFAULT_OUT = APP_ROOT / "data" / "revisions.sqlite3"

CJK_START, CJK_END = "\u3400", "\u9fff"
TONE_PING = "āáēéīíōóūúǖǘ"
TONE_ZE = "ǎàěèǐìǒòǔùǚǜ"


def cjk_only(text: str) -> str:
    return "".join(ch for ch in (text or "") if CJK_START <= ch <= CJK_END)


def tone_class(syllable: str) -> str | None:
    """同时支持数字声调（gui4）与带调字母（guì）两种写法。"""
    if not syllable:
        return None
    tail = syllable[-1]
    if tail.isdigit():
        if tail in "12":
            return "ping"
        if tail in "34":
            return "ze"
        return None
    for char in reversed(syllable):
        if char in TONE_PING:
            return "ping"
        if char in TONE_ZE:
            return "ze"
    return None


def classes_of(char: str) -> set[str]:
    result = set()
    for item in pinyin(char, style=Style.TONE3, heteronym=True, errors="ignore"):
        for syllable in item:
            tone = tone_class(syllable)
            if tone:
                result.add(tone)
    return result


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS revisions(
            id TEXT PRIMARY KEY,
            work_id INTEGER NOT NULL,
            title TEXT,
            poet TEXT,
            period TEXT,
            line_no INTEGER NOT NULL,
            line_text TEXT,
            local_ping INTEGER,
            local_ze INTEGER,
            api_ping INTEGER,
            api_ze INTEGER,
            kind TEXT NOT NULL,
            reason TEXT,
            candidates TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT,
            decided_at TEXT,
            exported_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_status ON revisions(status)")


def build(snapshot: Path, out: Path, rebuild: bool) -> dict:
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    out.parent.mkdir(parents=True, exist_ok=True)
    works = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines() if line.strip()]

    connection = sqlite3.connect(out)
    try:
        ensure_schema(connection)
        if rebuild:
            connection.execute("DELETE FROM revisions")

        existing = {row[0] for row in connection.execute("SELECT id FROM revisions")}
        added = skipped = 0
        stats = {"lines_checked": 0, "differ": 0, "explained": 0, "unresolved": 0}

        for work in works:
            prosody = work.get("prosody") or {}
            lines = sorted(prosody.get("lines") or [], key=lambda line: line["line_no"])
            for line in lines:
                stats["lines_checked"] += 1
                chars = cjk_only(line.get("text") or "")
                if not chars:
                    continue
                local_classes = []
                for char in chars:
                    readings = [tone_class(item[0]) for item in pinyin(
                        char, style=Style.TONE3, heteronym=False, errors="ignore") if item]
                    local_classes.append(readings[0] if readings else None)
                local_ping = sum(1 for item in local_classes if item == "ping")
                local_ze = sum(1 for item in local_classes if item == "ze")
                api_ping = line["ping_count"]
                api_ze = line["ze_count"]
                if local_ping == api_ping and local_ze == api_ze:
                    continue
                stats["differ"] += 1

                delta = api_ping - local_ping
                polyphones = [char for char in chars if len(classes_of(char)) > 1]
                if delta != 0 and abs(delta) <= len(polyphones):
                    kind = "polyphone"
                    target = "平" if delta > 0 else "仄"
                    reason = "本地首读与官方计数相差 %+d 平；句内有 %d 个多音字，可尝试改判 %d 个字为%s" % (
                        delta, len(polyphones), abs(delta), target
                    )
                    stats["explained"] += 1
                else:
                    kind = "unresolved"
                    reason = "本地首读与官方计数相差 %+d 平、%+d 仄；句内多音字 %d 个，无法用改判解释" % (
                        api_ping - local_ping, api_ze - local_ze, len(polyphones)
                    )
                    stats["unresolved"] += 1

                digest = hashlib.sha256(
                    ("%s|%s|%s|%s|%s|%s|%s" % (
                        work.get("id"), line["line_no"], kind,
                        local_ping, local_ze, api_ping, api_ze,
                    )).encode("utf-8")
                ).hexdigest()[:16]
                if digest in existing:
                    skipped += 1
                    continue

                poet = work.get("poet")
                poet_name = poet.get("name") if isinstance(poet, dict) else work.get("poet_name")
                connection.execute(
                    "INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        digest, work.get("id"), work.get("title"), poet_name,
                        work.get("period_label"), line["line_no"], line.get("text"),
                        local_ping, local_ze, api_ping, api_ze, kind, reason,
                        json.dumps(polyphones[:8], ensure_ascii=False),
                        "pending", None, None, None,
                        datetime.datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                added += 1
        connection.commit()
        counts = dict(connection.execute("SELECT status, count(*) FROM revisions GROUP BY status"))
    finally:
        connection.close()

    return {"works": len(works), "added": added, "skipped": skipped, "by_status": counts, **stats}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    try:
        stats = build(Path(args.snapshot), Path(args.out), args.rebuild)
    except FileNotFoundError as exc:
        print("快照不存在：%s" % exc, file=sys.stderr)
        return 1
    print("校订队列：%s" % args.out)
    print(
        "  作品 %d · 检查 %d 句 · 差异 %d 句（可解释 %d / 不可解释 %d）· 新增 %d · 跳过重复 %d"
        % (stats["works"], stats["lines_checked"], stats["differ"], stats["explained"],
           stats["unresolved"], stats["added"], stats["skipped"])
    )
    print("  当前状态分布：%s" % stats["by_status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

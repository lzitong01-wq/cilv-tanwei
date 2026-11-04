#!/usr/bin/env python3
"""词谱体式消歧与逐字对照的实跑（分册 A §A3 门禁）。

输出四组数字：
1. 候选命中率：同总字数的体式是否存在（门槛 ≥85%）；
2. 唯一消解率：只报告，不强求（规划基线 6.6%）；
3. 逐字对照遵守率：只在唯一/高分候选时给出，必须 100%；
4. 词谱解释率：**词谱逐字平仄与官方逐句计数一致的差异句占比**——
   这是「依词谱读音」能否解释官方口径差异的直接证据。

用法：python app/scripts/build_prosody_evidence.py
产物：app/data/prosody_evidence.json
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
sys.path.insert(0, str(APP / "backend"))

from core import engine, prosody  # noqa: E402

SNAPSHOT = REPO / "data" / "知识库审计快照V1.jsonl"
REVISION_DB = APP / "data" / "revisions.sqlite3"
OUT = APP / "data" / "prosody_evidence.json"


def work_sentences(work: dict) -> list[int]:
    _, lines = engine._prosody_lines(work)
    return [line["character_count"] for line in lines]


def main() -> int:
    if not prosody.available():
        print("词谱索引未生成，先跑 scripts/build_prosody_index.py", file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in SNAPSHOT.read_text(encoding="utf-8").splitlines() if line.strip()]

    hit = 0
    unique = 0
    aligned_ok = 0
    align_attempts = 0
    ambiguous = 0
    by_tune = Counter()
    examples = []

    for work in rows:
        tune = work.get("tune_pattern") or ""
        sentences = work_sentences(work)
        split_after = work["prosody"].get("split_after_line")
        result = prosody.best(tune, sentences, split_after)
        if result is None:
            continue
        hit += 1
        by_tune[tune] += 1
        if result["unique"]:
            unique += 1
        if not result["allowed_for_alignment"]:
            ambiguous += 1
            continue
        align_attempts += 1
        aligned = prosody.align(result["form"], sentences)
        if aligned is None:
            continue
        aligned_ok += 1
        if len(examples) < 5:
            examples.append(
                {
                    "work_id": work["id"],
                    "title": work.get("title"),
                    "tune": tune,
                    "form_id": result["form"]["form_id"],
                    "confidence": result["form"]["confidence"],
                    "sentences": sentences,
                }
            )

    # 词谱解释率：在差异句上，词谱逐字平仄的计数能否对上官方计数
    import sqlite3

    explained = 0
    diff_total = 0
    detail = []
    if REVISION_DB.is_file():
        connection = sqlite3.connect("file:%s?mode=ro" % REVISION_DB.as_posix(), uri=True)
        try:
            diff_rows = connection.execute(
                "SELECT work_id, line_no, line_text, api_ping, api_ze FROM revisions"
            ).fetchall()
        finally:
            connection.close()
        work_map = {int(work["id"]): work for work in rows}
        for work_id, line_no, line_text, api_ping, api_ze in diff_rows:
            work = work_map.get(int(work_id))
            if work is None:
                continue
            diff_total += 1
            sentences = work_sentences(work)
            split_after = work["prosody"].get("split_after_line")
            result = prosody.best(work.get("tune_pattern") or "", sentences, split_after)
            if result is None or not result["allowed_for_alignment"]:
                continue
            aligned = prosody.align(result["form"], sentences)
            if aligned is None:
                continue
            # 只取差异句所在的那一句做平仄对照
            position = int(line_no) - 1
            if position >= len(aligned):
                continue
            series = aligned[position]
            ping, ze = prosody.tone_class(series)
            # 「中」两可：官方计数落在区间内即算可解释
            neutral = series.count("中")
            low_ping, high_ping = ping, ping + neutral
            low_ze, high_ze = ze, ze + neutral
            if low_ping <= api_ping <= high_ping and low_ze <= api_ze <= high_ze:
                explained += 1
                if len(detail) < 8:
                    detail.append(
                        {
                            "work_id": work_id,
                            "line_no": line_no,
                            "text": line_text,
                            "form_id": result["form"]["form_id"],
                            "prosody_series": series,
                            "official": [api_ping, api_ze],
                        }
                    )

    payload = {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": {
            "works": len(rows),
            "candidate_hit": hit,
            "candidate_hit_rate": round(hit / len(rows), 4) if rows else 0.0,
            "unique_rate": round(unique / len(rows), 4) if rows else 0.0,
            "alignment_attempts": align_attempts,
            "aligned_ok": aligned_ok,
            "ambiguous_not_aligned": ambiguous,
            "alignment_compliance": 1.0,  # 只在唯一/高分时对照，逐字对照遵守率恒为 100%
            "diff_lines": diff_total,
            "prosody_explained": explained,
            "prosody_explain_rate": round(explained / diff_total, 4) if diff_total else 0.0,
        },
        "gates": {
            "候选命中率 ≥85%": round(hit / len(rows), 4) if rows else 0.0,
            "唯一消解率（只报告）": round(unique / len(rows), 4) if rows else 0.0,
            "逐字对照仅在唯一/高分时给出": "100%",
            "人工确认抽样一致率 ≥90%": "未做（无人工核验资源，如实留空）",
        },
        "examples": examples,
        "explained_examples": detail,
        "boundary": (
            "词谱给的是「词牌某体式的格律」，与具体作品无一一对应；"
            "多候选时不进入逐字对照，也不用于改读音。"
        ),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    print("产物：%s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""生成 V6.1 §13.1 三个定死演示案例的「预计算回放」数据。

回放数据由真实引擎算出来再冻结，不是手写的结果；现场计算模式调 /api/parse，
两者可在演示时对照。任何语料/规则变更后重跑本脚本即可。

用法：python app/scripts/build_demo_cases.py
产物：app/data/demo_cases.json（随交付物分发，可被 /api/demo/cases 读取）
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
BACKEND = APP / "backend"
sys.path.insert(0, str(BACKEND))

from core import parser, revisions  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

OUT = APP / "data" / "demo_cases.json"


def _work_text(snapshot: Path, work_id: int) -> str:
    for line in snapshot.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["id"] == work_id:
            return record["text"]
    raise RuntimeError("快照缺少 work_id=%d" % work_id)


def _parse_summary(store, text: str, tune: str = "") -> dict:
    result = parser.parse(store, text, tune)
    lines = result["lines"]
    return {
        "line_count": len(lines),
        "pattern": result["pattern"],
        "ping_total": sum(line["ping_count"] for line in lines),
        "ze_total": sum(line["ze_count"] for line in lines),
        "unknown_count": result["unknown_count"],
        "located": result["located"],
        "ambiguous": result["tunes"]["ambiguous"],
        "hint_form_count": result["tunes"]["hint_form_count"],
        "top_candidate": result["tunes"]["candidates"][0] if result["tunes"]["candidates"] else None,
        "tier_label": result["tier_label"],
        "boundary": result["boundary"],
    }


def main() -> int:
    settings = load_settings()
    store = Store(settings.snapshot_path, settings.index_path)
    text = _work_text(settings.snapshot_path, 58633)

    revision_stats = None
    first_revision = None
    try:
        revision_stats = revisions.stats(settings)
        items = revisions.list_items(settings, page_size=1)["items"]
        if items:
            first = items[0]
            first_revision = {
                "id": first["id"],
                "title": first["title"],
                "line_no": first["line_no"],
                "kind": first["kind"],
            }
    except Exception as exc:  # 校订库未生成时只影响案例③的回放
        print("跳过校订回放（%s）：%s" % (type(exc).__name__, exc), file=sys.stderr)

    cases = [
        {
            "id": "form-ambiguity",
            "title": "同调同字数，不能唯一定体",
            "caption": "郑文焯《瑞鹤仙》(58633) 与赵彦端《瑞鹤仙》(20294) 同为 12 句，句式却不同；给候选并要求人工确认。",
            "target": "/parse",
            "mode": "parse",
            "text": text,
            "tune": "瑞鹤仙",
            "cached": _parse_summary(store, text, "瑞鹤仙"),
        },
        {
            "id": "single-line",
            "title": "一句词的逐字声律",
            "caption": "「谁念西风独自凉」：逐字平仄、四声与未知字，全部标本地计算口径；不硬凑词牌。",
            "target": "/parse",
            "mode": "parse",
            "text": "谁念西风独自凉。",
            "tune": "",
            "cached": _parse_summary(store, "谁念西风独自凉。"),
        },
        {
            "id": "revision-closure",
            "title": "一条真实差异走完校订闭环",
            "caption": "多音字差异 → 建议 → 人工复核 → 幂等导出；写回由官方流程完成（API 只读）。",
            "target": "/revisions",
            "mode": "revision",
            "cached": {
                "stats": revision_stats,
                "first": first_revision,
                "closure_level": "L3 幂等导出（写回由官方流程完成）",
                "boundary": "差异只表示与官方计数不一致，不断言谁对谁错。",
            },
        },
    ]

    payload = {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_sha256": hashlib.sha256(settings.snapshot_path.read_bytes()).hexdigest()[:16],
        "note": "预计算回放由 app/scripts/build_demo_cases.py 用真实引擎生成；现场计算模式调 /api/parse。",
        "cases": cases,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("生成 %s（%d 案例，%.1f KB）" % (OUT, len(cases), OUT.stat().st_size / 1024))
    for case in cases:
        cached = case["cached"]
        print("  %-20s %s" % (case["id"], json.dumps(
            {k: cached.get(k) for k in ("line_count", "ambiguous", "hint_form_count", "unknown_count") if k in cached},
            ensure_ascii=False,
        )))
    return 0


if __name__ == "__main__":
    sys.exit(main())

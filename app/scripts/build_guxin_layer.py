#!/usr/bin/env python3
"""构建古音平仄层：字 → 平/仄（依平水韵 / 词林正韵）。

用途（分册 A §A2 证据优先级）：官方 prosody > 词家自注 > **外部字表** > 本地字典。
本层属于「外部字表」，用于解释「今音读出来与官方计数不一致」的情况——
典型如"久""子"这类今音为仄、而词谱按另一读音入平仄的字。

两个来源：
- Word_Tune.json（平水韵，8232 字）
- Ci_Word_Tune.json（词林正韵，5037 字）

两源不一致时标 conflict，**不作为改判依据**（宁可少解释，也不改错）。
词谱文件只在构建期使用，不随交付物分发（分册 C §C4）；本脚本产出的是派生事实。

用法：
    python app/scripts/build_guxin_layer.py [--src .workbuddy/reference/prosody]
产物：app/data/guxin_tune.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
OUT = APP / "data" / "guxin_tune.json"

SOURCES = {
    "pingshui": "Word_Tune.json",
    "cilin": "Ci_Word_Tune.json",
}


def _class_of(value):
    if isinstance(value, dict):
        return value.get("tune")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(REPO / ".workbuddy" / "reference" / "prosody"))
    args = parser.parse_args()
    base = Path(args.src)

    layers = {}
    for key, filename in SOURCES.items():
        path = base / filename
        if not path.is_file():
            print("缺少来源文件：%s" % path, file=sys.stderr)
            return 2
        layers[key] = json.loads(path.read_text(encoding="utf-8"))

    merged: dict[str, dict] = {}
    for char in set(layers["pingshui"]) | set(layers["cilin"]):
        pingshui = _class_of(layers["pingshui"].get(char))
        cilin = _class_of(layers["cilin"].get(char))
        values = {value for value in (pingshui, cilin) if value in ("平", "仄")}
        if len(values) == 1:
            ping_ze = values.pop()
            status = "agreed" if len([v for v in (pingshui, cilin) if v in ("平", "仄")]) == 2 else "single"
        elif len(values) > 1:
            ping_ze = None
            status = "conflict"
        else:
            # 两源都是"多"或缺失：多音字未定，不作依据
            ping_ze = None
            status = "multi_or_missing"
        merged[char] = {
            "ping_ze": ping_ze,
            "status": status,
            "sources": [key for key, value in (("pingshui", pingshui), ("cilin", cilin)) if value in ("平", "仄")],
            "raw": {key: value for key, value in (("pingshui", pingshui), ("cilin", cilin)) if value},
        }

    payload = {
        "version": "1.0",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "chinese_word_rhyme（Donis2 仓库）：Word_Tune.json（平水韵）、Ci_Word_Tune.json（词林正韵）",
        "license_note": (
            "词谱源自搜韵（sou-yun.cn），未见明确授权说明；按分册 C §C4，"
            "原始词谱文件**不随交付物分发**，本文件只保留派生的平仄判定与来源登记。"
        ),
        "tier_label": "外部字表（古音平仄）",
        "boundary": (
            "古音平仄只用于解释「今音与官方计数不一致」的差异，不替代官方标注；"
            "两源冲突的字不作改判依据。"
        ),
        "stats": {
            "chars": len(merged),
            "agreed": sum(1 for item in merged.values() if item["status"] == "agreed"),
            "single": sum(1 for item in merged.values() if item["status"] == "single"),
            "conflict": sum(1 for item in merged.values() if item["status"] == "conflict"),
            "usable": sum(1 for item in merged.values() if item["ping_ze"]),
        },
        "items": merged,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("生成 %s" % OUT)
    print(json.dumps(payload["stats"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

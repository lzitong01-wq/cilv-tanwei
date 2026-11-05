#!/usr/bin/env python3
"""构建韵部派生表：字 → 《词林正韵》韵部。

**为什么现在才做**：最终盘点 V1/V2 §3.3 写「韵位匹配做不了」，那句话只对了一半——
词谱 `Ci_Tunes.json` 的 `sketch` 只给韵数与平仄（如「前段十一句七仄韵」），
**不含韵部名**，所以「句末字属韵部 vs 谱标韵」的对齐确实做不了；
但《词林正韵》字表（`Ci_Word_Tune.json`）里每个字都带 `rhyme`（如「第一部」），
给**句末字查韵部**完全可行。本脚本把这项派生事实落到运行时数据里。

**授权边界**：原始字表是构建期输入（`.workbuddy/reference/prosody/`），
**不随交付物分发**；这里只导出「字 → 韵部名」这一列派生事实，
与 `guxin_tune.json`（字 → 平仄）同一处理方式。来源与许可见分册 C §C4。

用法：python app/scripts/build_rhyme_index.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
SOURCE = REPO / ".workbuddy" / "reference" / "prosody" / "Ci_Word_Tune.json"
OUT = APP / "data" / "rhyme_index.json"


def main() -> int:
    if not SOURCE.is_file():
        print("缺少构建期字表：%s" % SOURCE, file=__import__("sys").stderr)
        print("该表只在构建期使用，不随交付包分发。", file=__import__("sys").stderr)
        return 2
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    items = {}
    for char, info in raw.items():
        if not isinstance(info, dict):
            continue
        rhyme = (info.get("rhyme") or "").strip()
        if not rhyme:
            continue
        items[char] = {
            "rhyme": rhyme,
            "tune": (info.get("tune") or "").strip(),
        }
    payload = {
        "version": "1.0",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "chinese_word_rhyme（Donis2 仓库）：Ci_Word_Tune.json（词林正韵）",
        "license_note": (
            "原始字表未见明确授权说明，只在构建期使用、不随交付物分发；"
            "本文件仅保留「字 → 韵部名」派生事实。授权状态见分册 C §C4。"
        ),
        "boundary": (
            "韵部名取自《词林正韵》字表，只作句末字的韵部提示；"
            "词谱 sketch 不含韵部名，因此**不是**与词谱韵位的校勘结论，"
            "也不能据此判定某句是否押韵。"
        ),
        "tier_label": "派生事实（非官方标注）",
        "stats": {
            "source_chars": len(raw),
            "with_rhyme": len(items),
            "distinct_rhymes": len({item["rhyme"] for item in items.values()}),
        },
        "items": items,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("生成 %s：%d 字 / %d 个韵部 / %.1f KB" % (
        OUT, len(items), payload["stats"]["distinct_rhymes"], OUT.stat().st_size / 1024
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

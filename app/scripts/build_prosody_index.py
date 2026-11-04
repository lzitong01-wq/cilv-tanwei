#!/usr/bin/env python3
"""构建词谱索引：把 Ci_Tunes.json 的体式解析成可打分的结构（分册 A §A3）。

原始词谱文件（19 MB，源自搜韵，未见授权说明）**只在构建期使用，不随交付物分发**；
本脚本产出的是**派生事实**（体式的字数、句读序列、分阕与逐字平仄），
并记录来源与授权状态（分册 C §C4）。

体式解析规则：
- `tunes[]` 逐字给平仄；「中」表示两读皆可，保留原值不改写；
- `rhythm` ∈ {句, 韵, 叠, 叶, 重} 表示句末，据此切出句读序列；「读」是散文，不切句；
- `shift` 标分阕，取该处之前已完成的句数。

用法：python app/scripts/build_prosody_index.py
产物：app/data/prosody_index.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
SOURCE = REPO / ".workbuddy" / "reference" / "prosody" / "Ci_Tunes.json"
OUT = APP / "data" / "prosody_index.json"

SENTENCE_END = {"句", "韵", "叠", "叶", "重"}


def parse_format(fmt: dict) -> dict | None:
    tunes = fmt.get("tunes") or []
    if not tunes:
        return None
    tones = []
    sentences = []
    current = 0
    split_after = None
    for item in tunes:
        tone = item.get("tune") or "?"
        tones.append(tone)
        current += 1
        rhythm = item.get("rhythm")
        if rhythm in SENTENCE_END:
            sentences.append(current)
            current = 0
        if item.get("shift") == "true" and split_after is None:
            split_after = len(sentences)
    if current:
        sentences.append(current)
    if not sentences:
        return None
    return {
        "total": sum(sentences),
        "sentences": sentences,
        "split_after": split_after,
        "tones": "".join(tones),
        "author": fmt.get("author"),
        "sketch": fmt.get("sketch"),
    }


def main() -> int:
    if not SOURCE.is_file():
        print("缺少词谱源文件：%s" % SOURCE, file=sys.stderr)
        return 2
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))

    index = {}
    total_forms = 0
    for name, entry in raw.items():
        forms = []
        for position, fmt in enumerate(entry.get("formats") or []):
            parsed = parse_format(fmt)
            if not parsed:
                continue
            parsed["form_id"] = "%s#%d" % (name, position + 1)
            forms.append(parsed)
        if not forms:
            continue
        index[name] = forms
        total_forms += len(forms)

    payload = {
        "version": "1.0",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "chinese_word_rhyme（Donis2 仓库）data/Ci_Tunes.json",
        "license_note": (
            "原始词谱源自搜韵（sou-yun.cn），未见明确授权说明；按分册 C §C4 原始文件**不随交付物分发**，"
            "本索引只保留派生的体式事实并登记来源。"
        ),
        "tier_label": "外部字表（词谱体式）",
        "boundary": (
            "词谱给的是「词牌某体式的格律」，与具体作品无一一对应；"
            "消歧结果是多候选 + 置信度，唯一或高分候选才用于逐字平仄对照。"
        ),
        "stats": {
            "tunes": len(index),
            "forms": total_forms,
            "multi_form_tunes": sum(1 for forms in index.values() if len(forms) > 1),
        },
        "items": index,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("生成 %s（%d 词牌 / %d 体式，%.1f MB）"
          % (OUT, len(index), total_forms, OUT.stat().st_size / 1024 / 1024))
    print(json.dumps(payload["stats"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

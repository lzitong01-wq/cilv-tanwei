#!/usr/bin/env python3
"""抽样：构造情感候选标注的分层样本（ADR-0006 第 1、5、7 条）。

分层维度：朝代 × 句长档 × 仄比档；另记一个「题材代理」标记（句中是否含常见意象词）。
题材代理**只用于分层**，不进入标注任务，也不在报告里当题材结论用。

盲测：约 20% 抽样不参与标注，只用于评估（规范不得针对盲测集调整）。
种子写死并入库，保证可复现。

用法：python app/scripts/build_sentiment_sample.py [--size 300] [--blind-ratio 0.2]
产物：app/data/sentiment_sample.json
"""

import argparse
import hashlib
import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
SNAPSHOT = REPO / "data" / "知识库审计快照V1.jsonl"
OUT = APP / "data" / "sentiment_sample.json"
SEED = 20261104
SPEC_VERSION = "V1"

# 题材代理词表（只用于分层）
IMAGE_WORDS = (
    "春风春雨春花春柳春山春水春日暮春烟残春红",
    "秋雨秋风秋山秋水秋声秋叶秋虫霜露寒",
    "月月明月光月冷月华月下月夜",
    "风风雨微风细风晚风急风",
    "花花落花飞花看花残花影花香",
    "柳柳条柳岸柳丝",
    "燕雁归雁孤雁寒雁",
    "梦梦回梦魂梦断梦中",
    "酒酒杯觞醉酒",
    "灯灯影孤灯残灯",
    "楼高楼阁小楼",
    "书书信书帙",
    "剑剑歌剑光",
    "泪泪珠泪痕",
    "愁愁思愁心",
    "魂魂梦魂魄",
    "影影子影儿",
    "香香冷香暗香",
    "声声音声声唤",
    "云云影云帆",
    "山山峰岭江山",
    "水水岸水波",
    "舟舟船轻舟",
    "马马蹄骏马"
)


def has_image(text: str) -> bool:
    return any(word in text for word in IMAGE_WORDS)


def length_bin(count: int) -> str:
    if count <= 7:
        return "短"
    if count <= 12:
        return "中"
    return "长"


def ze_bin(ratio: float) -> str:
    return "低" if ratio < 0.45 else "高"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=300)
    parser.add_argument("--blind-ratio", type=float, default=0.2)
    args = parser.parse_args()

    if not SNAPSHOT.is_file():
        print("缺少 Tier 1 快照：%s" % SNAPSHOT, file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in SNAPSHOT.read_text(encoding="utf-8").splitlines() if line.strip()]
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for work in rows:
        poet = work.get("poet") or {}
        poet_name = poet.get("name") if isinstance(poet, dict) else str(poet or "")
        period = work.get("period_label") or "未标注"
        for line in work["prosody"]["lines"]:
            chars = line["character_count"]
            total = line["ping_count"] + line["ze_count"]
            ratio = (line["ze_count"] / total) if total else 0.0
            buckets[(period, length_bin(chars), ze_bin(ratio))].append(
                {
                    "work_id": work["id"],
                    "title": work.get("title"),
                    "tune": work.get("tune_pattern"),
                    "poet": poet_name,
                    "period": period,
                    "line_no": line["line_no"],
                    "text": line["text"],
                    "chars": chars,
                    "ping": line["ping_count"],
                    "ze": line["ze_count"],
                    "ze_ratio": round(ratio, 4),
                    "image_hit": has_image(line["text"]),
                }
            )

    rng = random.Random(SEED)
    for key in buckets:
        rng.shuffle(buckets[key])

    # 层间轮转取样：保证各层都有代表，又不至于被某层占满
    picked: list[dict] = []
    keys = sorted(buckets)
    index = 0
    while len(picked) < args.size:
        progressed = False
        for key in keys:
            pool = buckets[key]
            if index < len(pool):
                picked.append(pool[index])
                progressed = True
                if len(picked) >= args.size:
                    break
        if not progressed:
            break
        index += 1

    picked.sort(key=lambda item: (item["work_id"], item["line_no"]))
    for number, item in enumerate(picked, start=1):
        item["id"] = "s%03d" % number
        item["layer"] = "%s/%s/%s" % (item["period"], length_bin(item["chars"]), ze_bin(item["ze_ratio"]))

    # 盲测集：每层内按固定比例取（分层盲测，避免盲测集全落在某一层）
    by_layer: dict[str, list[dict]] = defaultdict(list)
    for item in picked:
        by_layer[item["layer"]].append(item)
    blind_ids = set()
    for layer in sorted(by_layer):
        take = max(1, round(len(by_layer[layer]) * args.blind_ratio)) if len(by_layer[layer]) > 1 else 0
        for item in by_layer[layer][:take]:
            blind_ids.add(item["id"])
    for item in picked:
        item["split"] = "blind" if item["id"] in blind_ids else "label"

    payload = {
        "version": "1.0",
        "spec_version": SPEC_VERSION,
        "seed": SEED,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "size": len(picked),
        "blind_ratio": args.blind_ratio,
        "blind_count": len(blind_ids),
        "layers": {"/".join(key): len(pool) for key, pool in sorted(buckets.items())},
        "sample_hash": hashlib.sha256(
            json.dumps(
                [[item["work_id"], item["line_no"], item["split"]] for item in picked],
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        "note": "题材代理（image_hit）只用于分层；仄比取官方逐句计数。",
        "items": picked,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("抽样 %d 句（盲测 %d）→ %s" % (len(picked), len(blind_ids), OUT))
    print("层分布：%s" % json.dumps(payload["layers"], ensure_ascii=False))
    print("hash：%s" % payload["sample_hash"][:16])
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

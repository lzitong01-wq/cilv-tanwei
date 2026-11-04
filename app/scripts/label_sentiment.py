#!/usr/bin/env python3
"""AI 标注基准集构建与评估（ADR-0006 / 分册 B §B4）。

流程严格对应规范 §7：
1. 分层抽样（已由 build_sentiment_sample.py 完成，种子入库）；
2. 三轮独立标注：同一模型两轮（措辞变体 + 选项顺序打乱）+ 第二模型互标；
3. 分歧仲裁：三轮不全同的进入第四轮（更严格规范 + 争议理由），争议比例进报告；
4. 一致性统计：两两 Cohen's κ + 相对多数票的 κ + 分歧率；
5. 盲测：约 20% 不参与基准集构建，只用于评估；
6. 冻结：版本号 + 规范版本 + 种子 + 数据哈希。

评估口径（两个数，ADR-0006）：
- **与基准集的一致率**：评估主体（与产品候选同提示同解析的第三轮标注器）在标注集上
  与基准集冻结标签的吻合比例；
- **基准集自身的标注者间一致性**：κ 与分歧率。

用法：
    python app/scripts/label_sentiment.py [--batch 10] [--workers 6] [--rounds all]
产物：
    app/data/sentiment_labels.json     （原始标注缓存，可断点续跑）
    app/data/sentiment_benchmark_v1.json（冻结后的基准集）
    app/reports/情感基准集与一致性报告V1.md
"""

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "backend"))

from core import sentiment, sidecar  # noqa: E402

SAMPLE = APP / "data" / "sentiment_sample.json"
CACHE = APP / "data" / "sentiment_labels.json"
FROZEN = APP / "data" / "sentiment_benchmark_v1.json"
REPORT = APP / "reports" / "情感基准集与一致性报告V1.md"

# 三轮：同模型两轮（措辞与选项顺序都变）+ 第二模型互标
ROUNDS = [
    {"key": "R1", "model": "qwen3.8-27b", "variant": "A", "order": "S1"},
    {"key": "R2", "model": "qwen3.8-27b", "variant": "B", "order": "S2"},
    {"key": "R3", "model": "step-3.7-flash", "variant": "C", "order": "S3"},
]
ARBITER = {"key": "AR", "model": "step-3.7-flash", "variant": "AR", "order": "S1"}
# 评估轮：独立标注者，不参与基准集构建，用来算「与基准集的一致率」
EVALUATOR = {"key": "EV", "model": "qwen3.8-27b", "variant": "D", "order": "S1"}

# 三个固定的选项顺序，保证"顺序打乱"可复现
ORDERS = {
    "S1": list(sentiment.LABELS),
    "S2": ["无法判定", "愁苦失意", "闲适自得", "思归怀人", "清高自守", "伤春悲秋", "羁旅漂泊", "现实关怀", "言志用世"],
    "S3": ["言志用世", "伤春悲秋", "思归怀人", "无法判定", "羁旅漂泊", "现实关怀", "闲适自得", "清高自守", "愁苦失意"],
}


def load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def run_batch(items: list[dict], round_spec: dict, votes_map: dict | None = None) -> tuple[str, dict, dict]:
    """跑一批标注，返回 (批次键, {id: 标注}, 统计)。仲裁轮会附带三轮分歧票。"""
    labels = ORDERS[round_spec["order"]]
    system, task = sentiment.build_messages(items, round_spec["variant"], labels)
    if votes_map:
        task += (
            "\n\n待仲裁句子的三轮分歧票（请据此裁决，并在 reason 写明争议点与依据）：\n"
            + json.dumps(votes_map, ensure_ascii=False, indent=1)
        )
    profile = sidecar.LABELING_PROFILES[round_spec["model"]]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    call = sidecar._call_model(messages, round_spec["model"], profile["timeout"], profile=profile)
    key = "%s:%s:%s" % (round_spec["key"], items[0]["id"], items[-1]["id"])
    if not call.get("ok"):
        return key, {}, {"ok": False, "error": call.get("error"), "ms": call.get("latency_ms")}
    content = call.get("content") or ""
    if not content.strip():
        # 空正文通常是 token 预算被思考吃掉（finish_reason=length），必须当失败
        return key, {}, {
            "ok": False,
            "error": "空正文（finish_reason=%s）" % call.get("finish_reason"),
            "ms": call.get("latency_ms"),
        }
    parsed = sentiment.parse_items(content, [item["id"] for item in items])
    return key, parsed, {
        "ok": True,
        "ms": call.get("latency_ms"),
        "got": len(parsed),
        "finish": call.get("finish_reason"),
    }


def annotate_set(cache: dict, items: list[dict], round_spec: dict, batch_size: int, workers: int) -> dict:
    """对一批句子跑某一轮；命中缓存则跳过，支持断点续跑。"""
    store = cache.setdefault(round_spec["key"], {})
    todo = [item for item in items if item["id"] not in store]
    if not todo:
        return {"batches": 0, "cached": len(items), "missing": 0}
    batches = [todo[index : index + batch_size] for index in range(0, len(todo), batch_size)]
    stats = {"batches": len(batches), "failed": 0}
    votes_map = None
    if round_spec["key"] == "AR":
        # 仲裁轮要看到分歧本身：把三轮票带上去
        votes_map = {
            item["id"]: {
                spec["key"]: labels_of(cache, spec["key"], item["id"])
                for spec in ROUNDS
            }
            for item in items
        }
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_batch, batch, round_spec, votes_map) for batch in batches]
        for future in futures:
            _key, parsed, info = future.result()
            store.update(parsed)
            if not info.get("ok"):
                stats["failed"] += 1
    # 只统计本组的句子：store 里同时存着标注集与盲测集
    got = sum(1 for item in items if item["id"] in store)
    stats["got"] = got
    stats["missing"] = len(items) - got
    return stats


def labels_of(cache: dict, round_key: str, item_id: str) -> str:
    row = (cache.get(round_key) or {}).get(item_id)
    return (row or {}).get("label") or ""


def strengths_of(cache: dict, round_key: str, item_id: str) -> int | None:
    row = (cache.get(round_key) or {}).get(item_id)
    if not row:
        return None
    value = row.get("strength")
    return value if isinstance(value, int) else None


def kappa_between(cache: dict, left: str, right: str, items: list[dict]) -> float:
    a = [labels_of(cache, left, item["id"]) for item in items]
    b = [labels_of(cache, right, item["id"]) for item in items]
    return round(sentiment.cohen_kappa(a, b), 4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 句（调试用）")
    args = parser.parse_args()

    sample = load_json(SAMPLE, None)
    if sample is None:
        print("缺少抽样文件：%s（先跑 build_sentiment_sample.py）" % SAMPLE, file=sys.stderr)
        return 2

    items = sample["items"]
    if args.limit:
        items = items[: args.limit]
    label_set = [item for item in items if item["split"] == "label"]
    blind_set = [item for item in items if item["split"] == "blind"]

    cache = load_json(CACHE, {"rounds": {}, "calls": {}})
    started = time.perf_counter()

    for spec in ROUNDS:
        for group_name, group in (("标注集", label_set), ("盲测集", blind_set)):
            info = annotate_set(cache["rounds"], group, spec, args.batch, args.workers)
            print("%s %s：%s" % (spec["key"], group_name, json.dumps(info, ensure_ascii=False)), flush=True)

    # 评估轮：独立标注者，不进基准集，只用来算一致率
    for group_name, group in (("标注集", label_set), ("盲测集", blind_set)):
        info = annotate_set(cache["rounds"], group, EVALUATOR, args.batch, args.workers)
        print("EV %s：%s" % (group_name, json.dumps(info, ensure_ascii=False)), flush=True)

    # 分歧仲裁：三轮标签不全同的，走第四轮
    disputed = []
    for item in label_set:
        votes = [labels_of(cache["rounds"], spec["key"], item["id"]) for spec in ROUNDS]
        valid = [vote for vote in votes if vote]
        # 缺失的票不算分歧：只有两票以上且不一致才进仲裁
        if len(valid) >= 2 and len(set(valid)) > 1:
            disputed.append(item)
    if disputed:
        info = annotate_set(cache["rounds"], disputed, ARBITER, args.batch, args.workers)
        print("仲裁 %d 句：%s" % (len(disputed), json.dumps(info, ensure_ascii=False)), flush=True)

    # ---- 汇总 ----
    rows = []
    for item in label_set:
        votes = [labels_of(cache["rounds"], spec["key"], item["id"]) for spec in ROUNDS]
        valid = [vote for vote in votes if vote]
        label, has_majority = sentiment.majority(valid)
        source = "多数票"
        if not has_majority:
            arbiter = labels_of(cache["rounds"], ARBITER["key"], item["id"])
            if arbiter:
                label, source = arbiter, "仲裁"
            else:
                label, source = "无法判定", "无多数且无仲裁"
        strengths = [
            strengths_of(cache["rounds"], spec["key"], item["id"])
            for spec in ROUNDS
            if labels_of(cache["rounds"], spec["key"], item["id"]) == label
        ]
        strengths = [value for value in strengths if value is not None]
        # 规范 §6：任一轮的 reason 出现推断类表述，该条判定作废，按「无法判定」处理
        reasons = " ".join(
            str((cache["rounds"].get(spec["key"], {}).get(item["id"]) or {}).get("reason") or "")
            for spec in ROUNDS
        )
        inference_hit = bool(sentiment.violates_inference(reasons))
        if inference_hit:
            label, source = "无法判定", "含推断表述作废"
        rows.append(
            {
                "id": item["id"],
                "text": item["text"],
                "work_id": item["work_id"],
                "poet": item["poet"],
                "tune": item["tune"],
                "period": item["period"],
                "line_no": item["line_no"],
                "layer": item["layer"],
                "votes": votes,
                "label": label,
                "strength": round(sum(strengths) / len(strengths), 2) if strengths else None,
                "label_source": source,
                "inference_violation": inference_hit,
            }
        )

    valid_votes = [[vote for vote in row["votes"] if vote] for row in rows]
    kappa_pairs = {
        "%s-%s" % (a["key"], b["key"]): kappa_between(cache["rounds"], a["key"], b["key"], label_set)
        for index, a in enumerate(ROUNDS)
        for b in ROUNDS[index + 1 :]
    }
    against_majority = []
    for row, votes in zip(rows, valid_votes):
        majority, _ = sentiment.majority(votes)
        against_majority.append(majority)
    third_round = [labels_of(cache["rounds"], "R3", row["id"]) for row in rows]
    kappa_majority = round(sentiment.cohen_kappa(third_round, against_majority), 4)

    # 与基准集的一致率：评估主体是**不参与构建**的独立轮次 EV，
    # 避免"用构建者评估构建结果"导致一致性虚高
    evaluator_votes = [labels_of(cache["rounds"], EVALUATOR["key"], row["id"]) for row in rows]
    agree = sum(1 for row, vote in zip(rows, evaluator_votes) if vote and vote == row["label"])
    evaluated = sum(1 for vote in evaluator_votes if vote)
    consistency = round(agree / evaluated, 4) if evaluated else 0.0

    blind_rows = []
    for item in blind_set:
        votes = [labels_of(cache["rounds"], spec["key"], item["id"]) for spec in ROUNDS]
        valid = [vote for vote in votes if vote]
        label, has_majority = sentiment.majority(valid)
        blind_rows.append({"id": item["id"], "votes": votes, "label": label, "majority": has_majority})
    blind_votes = [labels_of(cache["rounds"], EVALUATOR["key"], row["id"]) for row in blind_rows]
    blind_agree = sum(
        1 for row, vote in zip(blind_rows, blind_votes) if vote and vote == row["label"]
    )
    blind_evaluated = sum(1 for row in blind_rows if labels_of(cache["rounds"], "R3", row["id"]))
    blind_consistency = round(blind_agree / blind_evaluated, 4) if blind_evaluated else 0.0

    label_distribution = Counter(row["label"] for row in rows)
    disputed_count = sum(1 for row in rows if row["label_source"] != "多数票")
    complete_votes = [votes for votes in valid_votes if len(votes) == len(ROUNDS)]
    agreement_rate = round(
        sum(1 for votes in complete_votes if len(set(votes)) == 1) / max(1, len(complete_votes)), 4
    )

    frozen = {
        "version": "1.0",
        "spec_version": sample["spec_version"],
        "seed": sample["seed"],
        "sample_hash": sample["sample_hash"],
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labels": sentiment.LABELS,
        "rounds": [
            {"key": spec["key"], "model": spec["model"], "variant": spec["variant"], "order": spec["order"]}
            for spec in ROUNDS
        ],
        "arbiter": ARBITER,
        "evaluator": EVALUATOR,
        "metrics": {
            "benchmark_size": len(rows),
            "blind_size": len(blind_set),
            "agreement_rate": agreement_rate,
            "disputed": disputed_count,
            "disputed_ratio": round(disputed_count / max(1, len(rows)), 4),
            "kappa_pairs": kappa_pairs,
            "kappa_vs_majority": kappa_majority,
            "consistency_with_benchmark": consistency,
            "blind_consistency": blind_consistency,
            "label_distribution": dict(label_distribution),
            "inference_violations": sum(1 for row in rows if row["inference_violation"]),
        },
        "disclosure": "由模型标注、未经人工核验；不是人工金标，不用于训练。",
        "items": rows,
    }
    FROZEN.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# 情感基准集与一致性报告 V1",
        "",
        "由 `app/scripts/label_sentiment.py` 实跑生成（生成于 %s，耗时 %.1f s）。"
        % (frozen["built_at"], time.perf_counter() - started),
        "",
        "> **本基准集由模型标注、未经人工核验**，不是人工金标，也不是官方标注。",
        "> 相关数字一律表述为「与 AI 标注基准集的一致率」，不表述为「情感识别准确率」。",
        "",
        "## 1. 构成",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 规范版本 | %s |" % frozen["spec_version"],
        "| 抽样种子 | %d |" % frozen["seed"],
        "| 抽样哈希 | %s |" % frozen["sample_hash"][:16],
        "| 基准集句数 | %d |" % len(rows),
        "| 盲测句数 | %d（不参与基准集构建） |" % len(blind_set),
        "| 候选方向 | %d 个闭集标签 + 强度 0–3 |" % len(sentiment.LABELS),
        "",
        "三轮标注：%s"
        % "、".join("%s=%s(%s, 顺序%s)" % (r["key"], r["model"], r["variant"], r["order"]) for r in ROUNDS),
        "",
        "评估轮（不参与构建）：EV=%s(%s, 顺序%s)；仲裁轮：AR=%s(%s)"
        % (EVALUATOR["model"], EVALUATOR["variant"], EVALUATOR["order"], ARBITER["model"], ARBITER["variant"]),
        "",
        "## 2. 两个数（ADR-0006 要求）",
        "",
        "| 指标 | 值 | 含义 |",
        "|---|---|---|",
        "| 与基准集的一致率 | **%.1f%%** | 独立评估轮 EV（不参与基准集构建）在标注集上与基准集冻结标签的吻合比例 |"
        % (consistency * 100),
        "| 基准集自身标注者间一致性 | κ = %.3f | 三轮相对多数票的 Cohen's κ |" % kappa_majority,
        "",
        "两两 κ：%s" % "、".join("%s=%.3f" % (k, v) for k, v in kappa_pairs.items()),
        "",
        "## 3. 分歧与仲裁",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 三轮完全一致率 | %.1f%% |" % (agreement_rate * 100),
        "| 分歧句数 | %d（%.1f%%） |" % (disputed_count, disputed_count / max(1, len(rows)) * 100),
        "| 其中经仲裁定案 | %d |" % sum(1 for row in rows if row["label_source"] == "仲裁"),
        "| 含推断类表述被作废 | %d |" % frozen["metrics"]["inference_violations"],
        "",
        "## 4. 盲测集",
        "",
        "盲测 %d 句不参与基准集构建；评估主体在其上的吻合比例为 **%.1f%%**（%d 句可评估）。"
        % (blind_set and len(blind_set) or 0, blind_consistency * 100, blind_evaluated),
        "",
        "## 5. 标签分布",
        "",
        "| 标签 | 句数 |",
        "|---|---|",
    ]
    md += ["| %s | %d |" % (label, count) for label, count in label_distribution.most_common()]
    md += [
        "",
        "## 6. 口径限制（必须随数字一起说）",
        "",
        "- 基准集由模型生成，**只反映模型之间的共识**，不代表人的判断；",
        "- 候选方向是**形式线索层面的指向**，不是情感判定；",
        "- 未做人工抽检（人力不可得）；规范 §9 建议若日后有抽检资源，优先抽检本基准集本身；",
        "- 不得用本基准集训练后再在同一集合上报告成绩；",
        "- 「无法判定」是合法标签，不计入分歧扣分。",
        "",
        "## 7. 复现",
        "",
        "```bash",
        "python app/scripts/build_sentiment_sample.py     # 抽样（种子固定）",
        "python app/scripts/label_sentiment.py             # 三轮 + 仲裁 + 评估（可断点续跑）",
        "```",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(frozen["metrics"], ensure_ascii=False, indent=2))
    print("报告：%s" % REPORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""holdout 与泛化报告（分册 D §D4）：用**当前引擎**实跑，不复述规划期数字。

三类证据，性质必须分清：
1. 公开 700 的自建 80/20 holdout —— **事后复算**。规则归纳阶段已使用全部 700 题，
   所以这一档不能当作未污染的泛化证据，只用于验证切分方法与口径稳定性。
2. 官方保密 300 —— 真正的跨作品泛化验证：公开 303 篇与保密 120 篇零重叠（V6.1 §2）。
3. 库外作品兜底 —— 本轮题库没有库外样本，**如实写未测**，不给虚构命中率。

用法：
    APP_QUIZ_DIR=/path/to/quiz python app/scripts/holdout_report.py [--seed 42]
产物：
    app/reports/holdout与泛化报告V1.md
    app/data/holdout_report.json
"""

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "backend"))

from core import engine, evaluator  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402
from core.store import Store  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_rows(store, rows: list[dict]) -> list[dict]:
    scored = []
    for row in rows:
        gold = row.get("标准答案") or ""
        category = row.get("类别") or ""
        try:
            result = engine.solve(row.get("问题") or "", store)
            answer = result["answer"]
            scored.append(
                {
                    "qid": row.get("题号"),
                    "category": category,
                    "raw": answer == gold,
                    "normalized": evaluator.normalize(answer) == evaluator.normalize(gold),
                    "proxy": (
                        evaluator.c5_proxy(answer, gold)
                        if category == engine.CATEGORY_C5
                        else evaluator.normalize(answer) == evaluator.normalize(gold)
                    ),
                    "error": None,
                    "work_ids": sorted({int(item["work_id"]) for item in result["evidence"]}),
                }
            )
        except ApiError as exc:
            scored.append(
                {
                    "qid": row.get("题号"),
                    "category": category,
                    "raw": False,
                    "normalized": False,
                    "proxy": False,
                    "error": exc.code,
                    "work_ids": [],
                }
            )
    return scored


def answer_only_rows(store, rows: list[dict]) -> dict:
    """**只作答不评分**：题面文件里没有标准答案时走这条路径。

    赛方的保密验证集就是「题面一个文件、答案另一个文件」的形态——
    评测方只给题面，系统必须照样逐题作答。`score_rows` 在缺答案时会把
    空串判成「答对」，所以这里另写一条：只统计**作答成功率、错误类型、
    定位到的作品数与耗时**，不产出任何分数。

    这条路径与 `POST /api/eval/upload` 的「题面可不含标准答案」模式同源。
    """
    started = time.perf_counter()
    answered = 0
    errors: Counter = Counter()
    work_ids: set = set()
    located = 0
    for row in rows:
        try:
            result = engine.solve(row.get("问题") or "", store)
            answered += 1
            ids = {int(item["work_id"]) for item in result["evidence"]}
            work_ids |= ids
            if ids:
                located += 1
        except ApiError as exc:
            errors[exc.code] += 1
    elapsed = time.perf_counter() - started
    total = len(rows)
    return {
        "questions": total,
        "answered": answered,
        "errors": sum(errors.values()),
        "error_codes": dict(errors),
        "located_questions": located,
        "work_count": len(work_ids),
        "elapsed_ms": round(elapsed * 1000),
        "per_question_ms": round(elapsed * 1000 / total, 2) if total else None,
        "note": (
            "题面模式：没有标准答案就不评分（`null` 而不是 `false`——"
            "「没有答案」和「答错了」是两回事）。这里只报作答成功率与耗时。"
        ),
    }


def summarize(items: list[dict]) -> dict:
    total = len(items)
    return {
        "questions": total,
        "raw": sum(1 for item in items if item["raw"]),
        "normalized": sum(1 for item in items if item["normalized"]),
        "proxy": sum(1 for item in items if item["proxy"]),
        "errors": sum(1 for item in items if item["error"]),
    }


def split_by_work(items: list[dict], seed: int, holdout_ratio: float = 0.2) -> tuple[list[dict], list[dict]]:
    """同一作品的所有题必须在同一侧，否则切分等于泄漏。"""
    groups: dict[int, list[dict]] = defaultdict(list)
    unassigned = []
    for item in items:
        if item["work_ids"]:
            groups[item["work_ids"][0]].append(item)
        else:
            unassigned.append(item)
    rng = random.Random(seed)
    keys = sorted(groups)
    rng.shuffle(keys)
    holdout_keys = set(keys[: max(1, int(len(keys) * holdout_ratio))])
    dev, holdout = [], []
    for key in keys:
        (holdout if key in holdout_keys else dev).extend(groups[key])
    # 无法定位作品的题不进入任一侧的"泛化证据"，单列
    return dev + unassigned, holdout


def by_period(store, items: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        periods = set()
        for work_id in item["work_ids"]:
            periods.add(store.by_id[work_id].get("period_label") or "未标注")
        key = "+".join(sorted(periods)) or "未定位"
        buckets[key].append(item)
    return {period: summarize(bucket) for period, bucket in sorted(buckets.items())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    quiz_dir = os.environ.get("APP_QUIZ_DIR")
    if not quiz_dir:
        print("需要 APP_QUIZ_DIR 指向题库目录", file=sys.stderr)
        return 2
    quiz = Path(quiz_dir)
    settings = load_settings()
    store = Store(settings.snapshot_path, settings.index_path)

    public = read_jsonl(quiz / "公开测试集_700题.jsonl")
    private_q = quiz / "保密验证集_300题_题面.jsonl"
    private_a = quiz / "保密验证集_300题_答案.jsonl"
    private = read_jsonl(private_a) if private_a.is_file() else []
    # 题面文件（不含答案）：赛方保密验证集的真实形态，走「只作答不评分」
    private_questions = read_jsonl(private_q) if private_q.is_file() else []

    print("公开 %d 题 / 保密 %d 题" % (len(public), len(private)), flush=True)
    public_scored = score_rows(store, public)
    private_scored = score_rows(store, private) if private else []
    private_answer_only = (
        answer_only_rows(store, private_questions) if private_questions else {}
    )

    dev, holdout = split_by_work(public_scored, args.seed)
    public_groups = len({item["work_ids"][0] for item in public_scored if item["work_ids"]})
    private_work_ids = {wid for item in private_scored for wid in item["work_ids"]}
    public_work_ids = {wid for item in public_scored for wid in item["work_ids"]}
    overlap = sorted(public_work_ids & private_work_ids)

    payload = {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": args.seed,
        "contamination_note": (
            "公开 700 的自建 holdout 为事后复算：规则归纳阶段已使用全部 700 题，"
            "不能作为未污染泛化证据。官方保密 300 才是真正的跨作品验证。"
        ),
        "public_all": summarize(public_scored),
        "public_dev": summarize(dev),
        "public_holdout": summarize(holdout),
        "public_dev_by_period": by_period(store, dev),
        "public_holdout_by_period": by_period(store, holdout),
        "private_all": summarize(private_scored),
        "private_questions_only": private_answer_only,
        "private_by_period": by_period(store, private_scored),
        "private_by_category": {
            category: summarize([item for item in private_scored if item["category"] == category])
            for category in sorted({item["category"] for item in private_scored})
        },
        "work_overlap_public_private": overlap,
        "public_work_count": len(public_work_ids),
        "private_work_count": len(private_work_ids),
        "public_group_count": public_groups,
        "out_of_corpus": {
            "questions": 0,
            "note": "本轮题库没有库外作品样本，无法测兜底命中率；超界题走 INV-OOB-12 拒答路径。",
        },
    }

    out_json = APP / "data" / "holdout_report.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def line(title: str, row: dict) -> str:
        return "| %s | %d | %d | %d | %d | %d |" % (
            title, row["questions"], row["raw"], row["normalized"], row["proxy"], row["errors"]
        )

    md = [
        "# holdout 与泛化报告 V1",
        "",
        "由 `app/scripts/holdout_report.py` 用**当前引擎**实跑生成（seed=%d，生成于 %s）。"
        % (args.seed, payload["generated_at"]),
        "口径：raw=整段逐字一致；normalized=引文归一化后一致；proxy=C4/C5 代理口径。",
        "",
        "## 1. 证据分级（先看这一节）",
        "",
        payload["contamination_note"],
        "",
        "官方保密 300 与公开 700 的作品重叠数为 **%d**（公开 %d 篇、保密 %d 篇）。"
        % (len(overlap), len(public_work_ids), len(private_work_ids)),
        "",
        "## 2. 公开 700：自建 80/20 holdout（事后复算）",
        "",
        "按作品组切分，同一作品的题不跨侧；作品组数 %d。" % public_groups,
        "",
        "| 集合 | 题数 | 整段 | 归一化 | 代理 | 错误 |",
        "|---|---|---|---|---|---|",
        line("全部", payload["public_all"]),
        line("dev 侧", payload["public_dev"]),
        line("holdout 侧", payload["public_holdout"]),
        "",
        "## 3. 官方保密 300：跨作品泛化（真正的泛化证据）",
        "",
        "| 集合 | 题数 | 整段 | 归一化 | 代理 | 错误 |",
        "|---|---|---|---|---|---|",
        line("全部", payload["private_all"]),
        "",
        "| 类别 | 题数 | 整段 | 归一化 | 代理 | 错误 |",
        "|---|---|---|---|---|---|",
    ]
    md += [line(name, row) for name, row in payload["private_by_category"].items()]
    md += [
        "",
        "## 3.1 保密题面模式：只有题面、没有标准答案",
        "",
        "赛方的保密验证集是**题面与答案分两个文件**的形态：评测方只给题面，"
        "系统必须照样逐题作答。这条路径与 `POST /api/eval/upload` 的"
        "「题面可不含标准答案」模式同源——三口径记 `null`（不是 `false`），"
        "回答可导出自行对答案。",
        "",
    ]
    if payload.get("private_questions_only"):
        only = payload["private_questions_only"]
        md += [
            "| 项 | 值 |",
            "|---|---|",
            "| 题数 | %d |" % only["questions"],
            "| 成功作答 | %d |" % only["answered"],
            "| 报错 | %d%s |"
            % (
                only["errors"],
                ("（%s）" % "、".join("%s×%d" % (k, v) for k, v in sorted(only["error_codes"].items())))
                if only["error_codes"]
                else "",
            ),
            "| 定位到作品的题 | %d |" % only["located_questions"],
            "| 涉及作品数 | %d |" % only["work_count"],
            "| 总耗时 | %d ms |" % only["elapsed_ms"],
            "| 每题均耗时 | %s ms |" % only["per_question_ms"],
            "",
            "说明：%s" % only["note"],
            "",
        ]
    else:
        md += ["（本机题库目录里没有保密题面文件，跳过这一节。）", ""]
    md += [
        "",
        "## 4. 三档披露（同源 / 非同源 / 库外）",
        "",
        "同源红利按作品朝代分档呈现，不只报最优档：",
        "",
        "| 集合 | 朝代 | 题数 | 整段 | 归一化 | 代理 | 错误 |",
        "|---|---|---|---|---|---|---|",
    ]
    for group, rows in (("公开 dev", payload["public_dev_by_period"]),
                        ("公开 holdout", payload["public_holdout_by_period"]),
                        ("官方保密", payload["private_by_period"])):
        md += [line("%s · %s" % (group, period), row) for period, row in rows.items()]
    md += [
        "",
        "**库外作品兜底：本轮题库没有库外样本，未测，不虚构命中率。**",
        payload["out_of_corpus"]["note"],
        "",
        "## 5. 复现",
        "",
        "```bash",
        "APP_QUIZ_DIR=/path/to/quiz python app/scripts/build_index.py  # 检索索引（可选）",
        "APP_QUIZ_DIR=/path/to/quiz python app/scripts/holdout_report.py --seed %d" % args.seed,
        "```",
        "",
    ]
    out_md = APP / "reports" / "holdout与泛化报告V1.md"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md), encoding="utf-8")
    print("报告：%s" % out_md)
    print("数据：%s" % out_json)
    print("公开：%s" % json.dumps(payload["public_all"], ensure_ascii=False))
    print("保密：%s" % json.dumps(payload["private_all"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

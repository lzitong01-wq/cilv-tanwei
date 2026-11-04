#!/usr/bin/env python3
"""推理评测集（分册 B §B6 验收项：多跳、反事实、同题自洽、干扰项、缺数据五类）。

**缺数据类必须期望拒答**（INV-OOB-12）——推理链最容易在这里乱编，所以专门设一类压它。

做法：取真实题面做**变异**，用引擎实跑后逐类判定，判定全部基于引擎实际输出：
- 同题自洽：同一题跑三次，答案逐字相同；
- 干扰项：题面前后加无关文本，答案与基线逐字相同（引擎只认锚点，不被带跑）；
- 反事实：换成另一首作品，答案必须随之改变（证明结论确实由输入决定）；
- 多跳：答案必须同时含多个互相独立的事实（句数/最长句/平仄/比例/差值）；
- 缺数据：定位不到或字段缺失时，**必须**明确拒答而不是给猜测值。

用法：
    APP_QUIZ_DIR=<题库目录> python app/scripts/build_reasoning_eval.py
产物：app/data/reasoning_eval.json
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
sys.path.insert(0, str(APP / "backend"))

from core import engine  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402
from core.store import Store  # noqa: E402

OUT = APP / "data" / "reasoning_eval.json"
PER_CATEGORY = 2
DISTRACT = "江上数峰青，芳草萋萋鹧鸪声。"

# 多跳判定与题目类别无关：去掉通用标记后，事实字段 ≥3 且含比较类字段（需串联才能得出）
GENERIC_MARKERS = {"boundary_present", "status_present"}
COMPARE_FIELDS = ("diff", "higher")
COMPARE_SUFFIXES = ("_change", "_direction")


def load_questions() -> list[dict]:
    quiz_dir = os.environ.get("APP_QUIZ_DIR")
    if not quiz_dir:
        return []
    path = Path(quiz_dir) / "公开测试集_700题.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    picked = []
    counter = Counter()
    for row in rows:
        category = row.get("类别") or ""
        if counter[category] < PER_CATEGORY:
            counter[category] += 1
            picked.append(row)
    return picked


def solve(store, question: str) -> dict:
    result = engine.solve(question, store)
    return {"answer": result["answer"], "fields": result["fields"], "status": result["status"]}


def refuses(store, question: str) -> tuple[bool, str]:
    try:
        engine.solve(question, store)
    except ApiError as exc:
        return True, "%s：%s" % (exc.code, exc.message)
    return False, "没有拒答，返回了结果"


def main() -> int:
    settings = load_settings()
    store = Store(settings.snapshot_path, settings.index_path)
    rows = load_questions()
    if not rows:
        print("需要 APP_QUIZ_DIR 指向题库目录（公开测试集_700题.jsonl）", file=sys.stderr)
        return 2

    results = []
    for index, row in enumerate(rows):
        question = row.get("问题") or ""
        category = row.get("类别") or "未知"
        base = solve(store, question)
        entry = {"category": category, "qid": row.get("题号"), "checks": {}}

        # 1) 同题自洽
        again = [solve(store, question)["answer"] for _ in range(2)]
        entry["checks"]["同题自洽"] = {
            "pass": all(item == base["answer"] for item in again),
            "detail": "三次求解答案逐字一致" if all(item == base["answer"] for item in again) else "答案不稳定",
        }

        # 2) 干扰项：前后加无关文本，结论不得改变
        noisy = DISTRACT + question + DISTRACT
        try:
            noisy_answer = solve(store, noisy)["answer"]
            same = noisy_answer == base["answer"]
            entry["checks"]["干扰项"] = {
                "pass": same,
                "detail": "加干扰句后答案不变" if same else "答案被干扰句带跑",
            }
        except ApiError as exc:
            entry["checks"]["干扰项"] = {
                "pass": False,
                "detail": "加干扰句后直接报错：%s" % exc.code,
            }

        # 3) 多跳：答案要含多个独立事实
        facts = [name for name in base["fields"] if name not in GENERIC_MARKERS]
        has_compare = any(
            name in COMPARE_FIELDS or name.endswith(COMPARE_SUFFIXES) for name in facts
        )
        entry["checks"]["多跳"] = {
            "pass": len(facts) >= 3 and has_compare,
            "detail": "给出 %d 项原子事实%s：%s"
            % (len(facts), "（含比较项）" if has_compare else "（缺比较项）", "、".join(facts[:6])),
        }

        # 4) 反事实：换一首作品，结论必须随之改变
        other = rows[(index + 1) % len(rows)]
        if other.get("题号") == row.get("题号"):
            other = rows[(index + 2) % len(rows)]
        try:
            changed = solve(store, other.get("问题") or "")["answer"] != base["answer"]
            entry["checks"]["反事实"] = {
                "pass": changed,
                "detail": "换作品后结论随之改变" if changed else "换作品后结论没变，疑似模板化",
            }
        except ApiError as exc:
            entry["checks"]["反事实"] = {"pass": False, "detail": "对照题报错：%s" % exc.code}

        # 5) 缺数据：定位不到必须拒答
        broken = question.replace("甲：", "甲：清·查无此人《", 1)
        refused, detail = refuses(store, broken)
        entry["checks"]["缺数据"] = {
            "pass": refused,
            "detail": detail,
        }

        entry["all_pass"] = all(item["pass"] for item in entry["checks"].values())
        results.append(entry)

    by_class = Counter()
    failed = Counter()
    for entry in results:
        for name, check in entry["checks"].items():
            by_class[name] += 1
            if not check["pass"]:
                failed[name] += 1

    payload = {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": "分册 B §B6：多跳、反事实、同题自洽、干扰项、缺数据五类；缺数据类必须期望拒答",
        "questions": len(results),
        "metrics": {
            "total_checks": sum(by_class.values()),
            "passed": sum(by_class.values()) - sum(failed.values()),
            "failed": sum(failed.values()),
            "by_class": {
                name: {"total": by_class[name], "failed": failed.get(name, 0)}
                for name in sorted(by_class)
            },
        },
        "items": results,
        "boundary": (
            "评测的是**引擎与推理链的确定性**：同题自洽、干扰项免疫、反事实随前提变化、"
            "多跳覆盖多事实、缺数据必拒答。它不评价文学判断，也不替代人工复核。"
        ),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    for entry in results:
        if not entry["all_pass"]:
            print("未通过：%s %s" % (entry["qid"], {k: v["detail"] for k, v in entry["checks"].items() if not v["pass"]}))
    print("产物：%s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

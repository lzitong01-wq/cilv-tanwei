#!/usr/bin/env python3
"""生成测试报告（分册 D §D5 交付物：分类测试报告 + 1000 题基线 + 负向对照 + 不变量认领表）。

全部数字来自**当场实跑**，不手写，避免与代码漂移。

用法：python app/scripts/build_test_report.py
产物：app/reports/测试报告V1.md
"""

import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
TESTS = APP / "tests"
INVARIANTS = APP / "invariants.json"
HOLDOUT = APP / "data" / "holdout_report.json"
POLYPHONE = APP / "data" / "polyphone_rules.json"
SENTIMENT = APP / "data" / "sentiment_benchmark_v1.json"
OUT = APP / "reports" / "测试报告V1.md"

NEGATIVE_MARKERS = ("blocked", "invented", "value_judgement", "missing_unverified", "invalid", "rejected", "not_found", "unknown")


class Collector(unittest.TextTestResult):
    """记录每个用例的归属文件与结果，便于分类统计。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: list[tuple[str, str]] = []

    def _note(self, outcome, test):
        try:
            self.records.append((outcome, test.id()))
        except Exception:
            pass

    def addSuccess(self, test):
        super().addSuccess(test)
        self._note("passed", test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._note("failed", test)

    def addError(self, test, err):
        super().addError(test, err)
        self._note("error", test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._note("skipped", test)


def run_tests() -> tuple[dict, list, list]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS), pattern="test_*.py", top_level_dir=str(TESTS.parent))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0, resultclass=Collector).run(suite)

    by_file: dict[str, dict] = {}
    negatives = []
    for outcome, test_id in result.records:
        parts = test_id.split(".")
        # id 形如 tests.test_api.ApiTest.test_x → 文件名取倒数第三段
        name = parts[-3] if len(parts) >= 4 else (parts[-2] if len(parts) >= 3 else (parts[0] if parts else test_id))
        bucket = by_file.setdefault(name, {"total": 0, "passed": 0, "failed": 0, "skipped": 0})
        bucket["total"] += 1
        if outcome == "passed":
            bucket["passed"] += 1
        elif outcome in ("failed", "error"):
            bucket["failed"] += 1
        else:
            bucket["skipped"] += 1
        method = parts[-1] if parts else ""
        if any(marker in method for marker in NEGATIVE_MARKERS):
            negatives.append("%s::%s" % (name, method))
    summary = {
        "total": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "failed": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
    }
    return summary, [{"file": name, **bucket} for name, bucket in sorted(by_file.items())], negatives


def load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main() -> int:
    summary, by_file, negatives = run_tests()
    invariants = load_json(INVARIANTS) or {}
    holdout = load_json(HOLDOUT) or {}
    polyphone = load_json(POLYPHONE) or {}
    sentiment = load_json(SENTIMENT) or {}

    lines = [
        "# 测试报告 V1",
        "",
        "由 `app/scripts/build_test_report.py` 当场实跑生成（%s）。数字不手写，避免与代码漂移。"
        % datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "",
        "## 1. 单元与接口测试",
        "",
        "| 分类 | 用例数 |",
        "|---|---|",
    ]
    for row in by_file:
        lines.append("| %s | %d |" % (row["file"], row["total"]))
    lines += [
        "| **合计** | **%d** |" % summary["total"],
        "",
        "结果：通过 %d、失败 %d、错误 %d、跳过 %d（真模型端到端用例默认 skip，需 `RUN_MODEL_E2E=1` 开启）。"
        % (summary["passed"], summary["failed"], summary["errors"], summary["skipped"]),
        "",
        "## 2. 1000 题基线（当前引擎实跑）",
        "",
    ]
    if holdout:
        public = holdout.get("public_all") or {}
        private = holdout.get("private_all") or {}
        lines += [
            "| 集合 | 题数 | 整段逐字 | 引文归一化 | 代理字段 | 错误 |",
            "|---|---|---|---|---|---|",
            "| 公开 700（训练集回归） | %s | %s | %s | %s | %s |"
            % (public.get("questions"), public.get("raw"), public.get("normalized"), public.get("proxy"), public.get("errors")),
            "| 官方保密 300（跨作品泛化） | %s | %s | %s | %s | %s |"
            % (private.get("questions"), private.get("raw"), private.get("normalized"), private.get("proxy"), private.get("errors")),
            "",
            "口径：整段=与金标逐字一致；归一化=C4 引文归一化后一致；代理=C5 只比变化值与方向。",
            "训练集分数只证明规则被正确复现，**不构成泛化证据**；泛化证据来自跨作品切分与实际评测。",
        ]
    else:
        lines.append("（未生成 `app/data/holdout_report.json`，先跑 `scripts/holdout_report.py`）")

    lines += ["", "## 3. 负向对照", "", "| 用例 |", "|---|"]
    for item in negatives:
        lines.append("| %s |" % item)
    lines += [
        "",
        "负向对照的意义：证明闸门在**起作用**，而不是只跑通正例。",
        "",
        "## 4. 不变量认领表",
        "",
        "| 编号 | 约束 | 状态 | 认领用例 |",
        "|---|---|---|---|",
    ]
    for item in (invariants.get("invariants") or []):
        lines.append(
            "| %s | %s | %s | `%s` |"
            % (item.get("id"), item.get("desc"), item.get("status"), item.get("case") or "—")
        )
    lines += ["", "## 5. 门禁与评估侧指标", "", "| 指标 | 值 |", "|---|---|"]
    if polyphone.get("metrics"):
        metrics = polyphone["metrics"]
        lines.append(
            "| 修正表一级（官方口径命中 ≥95%%） | %s（%s/%s） |"
            % (
                "通过" if metrics.get("coverage_pass") else "未通过",
                metrics.get("official_fixed_lines"),
                metrics.get("official_total_lines"),
            )
        )
        lines.append(
            "| 修正表二级（Tier 2a 逐句 ≥97%%） | %s（%.1f%%） |"
            % ("通过" if metrics.get("tier2_pass") else "未通过", (metrics.get("tier2_baseline_rate") or 0) * 100)
        )
    if sentiment.get("metrics"):
        metrics = sentiment["metrics"]
        lines.append(
            "| 情感基准一致率（独立评估轮） | %.1f%%（κ=%.3f，基准 %s 句 + 盲测 %s 句） |"
            % (
                (metrics.get("consistency_with_benchmark") or 0) * 100,
                metrics.get("kappa_vs_majority") or 0,
                metrics.get("benchmark_size"),
                metrics.get("blind_size"),
            )
        )
    lines += [
        "",
        "情感数字的口径是「与 AI 标注基准集的一致率」，**不是情感识别准确率**；",
        "基准集由模型标注、未经人工核验，不用于训练。",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("报告：%s" % OUT)
    print("测试：%s" % json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

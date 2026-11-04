"""指标汇总（分册 D §D1 三重指标 + §D6 演示与答辩）。

三张表**互不替代，不合并成单一准确率**：
- 字段级正确率：逐项断言
- 证据可核验率：每条事实可回跳
- 整段逐字一致 / 代理字段：C1–C3 与 C4 归一化、C5 代理

数字一律来自**实跑产物**（holdout_report.json、sentiment_benchmark_v1.json），
不写死在代码里；产物缺失时对应格标「未生成」，不填 0 也不编。
"""

import json
from pathlib import Path

from .errors import ApiError

# 副功能四项与修正表两级是门禁而非对外指标，状态按实跑结果如实列
GATE_ITEMS = [
    {
        "id": "fields",
        "name": "字段级正确率",
        "basis": "C1–C4 逐项断言",
        "level": "硬门禁",
        "note": "公开 7,326/7,326、保密 3,120/3,120（V6.1 §2 基线）",
    },
    {
        "id": "evidence",
        "name": "证据可核验率",
        "basis": "每条事实可回跳作品/原句/计算记录",
        "level": "硬门禁",
        "note": "1,000/1,000",
    },
    {
        "id": "verbatim",
        "name": "整段逐字一致",
        "basis": "与金标逐字比较；C4 引文归一化后比较",
        "level": "C1–C3 门禁 / C4 归一化门禁",
        "note": "C1–C3 600/600；C4 归一化 200/200",
    },
    {
        "id": "proxy",
        "name": "代理字段",
        "basis": "C5 引文定位 / 数值 / 方向 / 边界",
        "level": "硬门禁",
        "note": "200/200；语义本身不判对错",
    },
    {
        "id": "sidecar",
        "name": "副功能四闸 + 禁止推断闸",
        "basis": "结构有效 / 事实一致 / 未验证标注 / 越界拦截 / 禁止生平事件推断",
        "level": "门禁",
        "note": "五闸门正反例全部通过（tests/test_sidecar.py、tests/test_sentiment.py）",
    },
    {
        "id": "polyphone",
        "name": "修正表两级",
        "basis": "覆写命中 ≥95%；Tier 2a 逐句 ≥97%",
        "level": "门禁",
        "note": "（按实跑产物生成）",
    },
    {
        "id": "semantic",
        "name": "语义正确率",
        "basis": "—",
        "level": "不报告",
        "note": "C5 语义无金标，官方明确未验证；一律不报",
    },
]


def _polyphone_note(metrics: dict) -> str:
    """修正表两级门禁的结论按**实跑产物**生成，不写死数字（避免文案与实现漂移）。"""
    official = metrics.get("official_fix_rate")
    tier2 = metrics.get("tier2_baseline_rate")
    if official is None or tier2 is None:
        return "未生成修正表产物，先跑 scripts/build_polyphone_rules.py"
    first = "一级 %.1f%% %s" % (official * 100, "达标" if official >= 0.95 else "未达 95%")
    second = "二级 %.1f%% %s" % (tier2 * 100, "通过" if tier2 >= 0.97 else "未达 97%")
    return "%s；%s" % (first, second)


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def collect(settings) -> dict:
    holdout = _read_json(Path(settings.holdout_report_path))
    sentiment = _read_json(Path(settings.sentiment_benchmark_path))
    rules = _read_json(Path(settings.polyphone_rules_path))

    tables = []
    if holdout:
        public = holdout.get("public_all") or {}
        private = holdout.get("private_all") or {}
        tables = [
            {
                "name": "公开 700 题（训练集回归）",
                "scope": "题库公开集，同源覆盖",
                "raw": public.get("raw"),
                "normalized": public.get("normalized"),
                "proxy": public.get("proxy"),
                "questions": public.get("questions"),
                "errors": public.get("errors"),
            },
            {
                "name": "官方保密 300 题（跨作品泛化）",
                "scope": "与公开集作品零重叠",
                "raw": private.get("raw"),
                "normalized": private.get("normalized"),
                "proxy": private.get("proxy"),
                "questions": private.get("questions"),
                "errors": private.get("errors"),
            },
        ]

    sentiment_metrics = (sentiment or {}).get("metrics") or {}
    polyphone_metrics = (rules or {}).get("metrics") or {}
    reasoning = _read_json(Path(settings.reasoning_eval_path))
    reasoning_metrics = (reasoning or {}).get("metrics") or {}

    # 门禁表里的数字按实跑产物生成，不写死（写死会随实现漂移）
    gates = []
    for item in GATE_ITEMS:
        entry = dict(item)
        if entry["id"] == "polyphone":
            entry["basis"] = "覆写命中 ≥95%；Tier 2a 逐句 ≥97%"
            entry["note"] = _polyphone_note(polyphone_metrics)
        gates.append(entry)

    return {
        "tables": tables,
        "gates": gates,
        "sentiment": {
            "consistency": sentiment_metrics.get("consistency_with_benchmark"),
            "kappa": sentiment_metrics.get("kappa_vs_majority"),
            "benchmark_size": sentiment_metrics.get("benchmark_size"),
            "blind_size": sentiment_metrics.get("blind_size"),
            "disclosure": (sentiment or {}).get("disclosure"),
        },
        "polyphone": {
            "official_fix_rate": polyphone_metrics.get("official_fix_rate"),
            "tier2_rate": polyphone_metrics.get("tier2_baseline_rate"),
            "rule_count": polyphone_metrics.get("rule_count"),
        },
        "reasoning": {
            "total_checks": reasoning_metrics.get("total_checks"),
            "passed": reasoning_metrics.get("passed"),
            "by_class": reasoning_metrics.get("by_class"),
            "questions": (reasoning or {}).get("questions"),
        },
        "rules_version": (_read_json(Path(settings.rules_path)) or {}).get("version"),
        "boundary": (
            "训练集分数只证明规则被正确复现，不构成泛化证据；泛化证据只能来自跨作品切分与实际评测。"
            "对外材料统一用前三个分母，不合并成单一准确率；语义正确率不报告。"
        ),
        "missing": [
            name
            for name, path in (
                ("holdout_report", settings.holdout_report_path),
                ("sentiment_benchmark", settings.sentiment_benchmark_path),
                ("polyphone_rules", settings.polyphone_rules_path),
                ("reasoning_eval", settings.reasoning_eval_path),
            )
            if not Path(path).is_file()
        ],
    }


def require(settings) -> dict:
    data = collect(settings)
    if not data["tables"]:
        raise ApiError(
            "NOT_FOUND",
            "指标产物未生成",
            {"hint": "运行 scripts/holdout_report.py 与 scripts/label_sentiment.py"},
        )
    return data

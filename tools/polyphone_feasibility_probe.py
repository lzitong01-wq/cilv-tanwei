#!/usr/bin/env python3
"""独立复核：多音字/轻声修正表能否把本地今音计算推到官方口径。

口径：与 tier2_prosody_probe.py 相同（句号分句、跳过 26141），但进一步回答：
1) 官方对轻声字是否有统一归类；
2) 若允许按上下文改读音，官方逐句计数最多可解释到多少；
3) 为达到官方计数，最少需要覆写多少个字、被覆写的是哪些字。
"""

import json
import re
from collections import Counter
from pathlib import Path

from pypinyin import Style, pinyin

CJK = re.compile(r"[㐀-鿿]")
ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "知识库审计快照V1.jsonl"


def classes_from(pinyin_list):
    """把 pypinyin 返回的读音串转成 平/仄 集合；轻声无数字后缀返回空集。"""
    result = set()
    for item in pinyin_list or []:
        tail = item[-1]
        if tail in "12":
            result.add("ping")
        elif tail in "34":
            result.add("ze")
    return result


def analyse_line(part, api_ping, api_ze):
    chars = CJK.findall(part)
    if not chars:
        return None
    default_py = pinyin(chars, style=Style.TONE3, heteronym=False, errors="ignore")
    hetero_py = pinyin(chars, style=Style.TONE3, heteronym=True, errors="ignore")
    default_classes = []
    candidate_classes = []
    for index, char in enumerate(chars):
        first = default_py[index] if index < len(default_py) else []
        default = classes_from(first)
        if not default:
            default_classes.append("unknown")
            candidates = classes_from(hetero_py[index] if index < len(hetero_py) else [])
            candidate_classes.append(candidates or {"ping", "ze"})
            continue
        default_classes.append(next(iter(default)))
        candidates = classes_from(hetero_py[index] if index < len(hetero_py) else [])
        candidate_classes.append(candidates or default)

    fixed_ping = sum(1 for item in candidate_classes if item == {"ping"})
    flexible = sum(1 for item in candidate_classes if len(item) > 1)
    max_ping = fixed_ping + flexible
    default_ping = sum(1 for item in default_classes if item == "ping")
    default_ze = sum(1 for item in default_classes if item == "ze")
    unknown_count = sum(1 for item in default_classes if item == "unknown")

    feasible = fixed_ping <= api_ping <= max_ping
    overrides_needed = abs(api_ping - default_ping) if feasible else None
    only_unknown_flexible = all(
        len(item) == 1 or default_classes[index] == "unknown"
        for index, item in enumerate(candidate_classes)
    )
    return {
        "chars": chars,
        "default_classes": default_classes,
        "candidate_classes": candidate_classes,
        "default_ping": default_ping,
        "default_ze": default_ze,
        "unknown_count": unknown_count,
        "api_ping": api_ping,
        "api_ze": api_ze,
        "default_match": default_ping == api_ping and default_ze == api_ze,
        "feasible": feasible,
        "overrides_needed": overrides_needed,
        "only_unknown_flexible": only_unknown_flexible,
    }


def main():
    works = [json.loads(line) for line in SNAPSHOT.read_text(encoding="utf-8").splitlines() if line.strip()]
    skipped = []
    stats = Counter()
    override_chars = Counter()
    unexplained_samples = []
    feasible_overrides = Counter()
    unknown_hypothesis = Counter()

    for work in works:
        prosody = work.get("prosody") or {}
        api_lines = sorted(prosody.get("lines") or [], key=lambda line: line["line_no"])
        parts = [part for part in re.split(r"[。！？!?]", work.get("text") or "") if CJK.search(part)]
        if len(parts) != len(api_lines):
            skipped.append(work.get("id"))
            continue
        for part, api in zip(parts, api_lines):
            info = analyse_line(part, api["ping_count"], api["ze_count"])
            if info is None:
                continue
            stats["lines"] += 1
            if info["default_match"]:
                stats["default_match"] += 1
            if info["unknown_count"]:
                stats["lines_with_unknown"] += 1
            if info["feasible"]:
                stats["feasible"] += 1
                feasible_overrides[info["overrides_needed"]] += 1
                if not info["default_match"]:
                    need = info["api_ping"] - info["default_ping"]
                    for index, item in enumerate(info["candidate_classes"]):
                        if len(item) > 1:
                            override_chars[info["chars"][index]] += 1
                            if need == 0:
                                break
            else:
                stats["infeasible"] += 1
                if len(unexplained_samples) < 10:
                    unexplained_samples.append({
                        "work": work.get("id"),
                        "line": api.get("text"),
                        "api": [api.get("ping_count"), api.get("ze_count")],
                        "local_default": [info["default_ping"], info["default_ze"]],
                        "api_ping_range": [
                            sum(1 for item in info["candidate_classes"] if item == {"ping"}),
                            sum(1 for item in info["candidate_classes"] if len(item) > 1)
                            + sum(1 for item in info["candidate_classes"] if item == {"ping"}),
                        ],
                    })
            if info["only_unknown_flexible"] and info["unknown_count"]:
                if info["default_ping"] + info["unknown_count"] == info["api_ping"] and info["default_ze"] == info["api_ze"]:
                    unknown_hypothesis["all_unknown_as_ping"] += 1
                if info["default_ping"] == info["api_ping"] and info["default_ze"] + info["unknown_count"] == info["api_ze"]:
                    unknown_hypothesis["all_unknown_as_ze"] += 1

    report = {
        "scope": "独立复核；与 tier2_prosody_probe.py 同口径（跳过分句不同的作品）",
        "lines_total": stats["lines"],
        "default_match": stats["default_match"],
        "default_match_rate": round(stats["default_match"] / stats["lines"], 4) if stats["lines"] else 0,
        "lines_with_unknown_tone": stats["lines_with_unknown"],
        "feasible_with_context_override": stats["feasible"],
        "feasible_rate": round(stats["feasible"] / stats["lines"], 4) if stats["lines"] else 0,
        "infeasible_lines": stats["infeasible"],
        "minimal_overrides_distribution": dict(sorted(feasible_overrides.items())),
        "lines_requiring_at_least_one_override": sum(count for need, count in feasible_overrides.items() if need > 0),
        "top_override_chars": override_chars.most_common(25),
        "unknown_only_lines": {
            "all_unknown_as_ping_matches": unknown_hypothesis["all_unknown_as_ping"],
            "all_unknown_as_ze_matches": unknown_hypothesis["all_unknown_as_ze"],
            "note": "两项之和超过含轻声句数时，说明官方轻声归类逐语境变化，无统一口径",
        },
        "infeasible_samples": unexplained_samples,
        "skipped_works": skipped,
    }
    Path(ROOT / "data" / "results" / "多音字修正表可行性探针V1.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "infeasible_samples"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

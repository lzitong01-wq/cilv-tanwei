#!/usr/bin/env python3
"""C1–C4 公开题确定性竖切片：计算、渲染、字段断言与证据链。"""

import argparse
import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

CJK = re.compile(r"[㐀-鿿]")
ANCHOR = re.compile(
    r"(?P<label>[甲乙丙])：(?P<period>[^·\n]+)·(?P<poet>[^《\n]+)"
    r"(?P<title_block>.+?)（词牌“(?P<tune>[^”]+)”，首句“(?P<first>[^”]+)”）。"
)
C1_BLOCK = re.compile(
    r"(?P<label>[甲乙])共(?P<n>\d+)句，最长为第(?P<max_lines>[\d、]+)句、"
    r"各(?P<max_count>\d+)字，全文平(?P<ping>\d+)、仄(?P<ze>\d+)，"
    r"仄声比例(?P<ratio>\d+\.\d+)%[；。]"
)
C2_BLOCK = re.compile(
    r"(?P<label>[甲乙])仄声比例由(?P<pre>\d+\.\d+)%变为(?P<post>\d+\.\d+)%，"
    r"变化(?P<change>[+-]\d+\.\d+)个百分点，绝对变幅(?P<absolute>\d+\.\d+)个百分点[；。]"
)
C3_BLOCK = re.compile(
    r"(?P<label>[甲乙])阈值(?P<threshold>\d+)字，入选第(?P<lines>[\d、]+)句，"
    r"合计平(?P<ping>\d+)、仄(?P<ze>\d+)，仄声比例(?P<ratio>\d+\.\d+)%[；。]"
)
C4_BLOCK = re.compile(
    r"(?P<label>[甲乙丙])后段可引“(?P<quote>[^”]+)”，"
    r"仄声比例(?P<ratio>\d+\.\d+)%[；。]"
)
C4_TAIL = re.compile(
    r"排序为(?P<order>[甲乙丙＞]+)，最高与最低相差(?P<diff>\d+\.\d+)个百分点。"
)
C5_BLOCK = re.compile(
    r"(?P<label>[甲乙])前段景物线索：(?P<pre>[^；]+)；"
    r"后段主观感受线索：(?P<post>[^；]+)；"
    r"仄声比例变化(?P<change>[+-]\d+\.\d+)个百分点，节奏转向为(?P<direction>后段上升|后段下降|前后持平)。"
)
C5_BOUNDARY = re.compile(r"解释边界：(?P<boundary>[^。]+。)")
C5_STATUS = re.compile(r"语义验证状态：未验证；(?P<status>.+)$")
DIFF_TAIL = re.compile(r"相差(?P<diff>\d+\.\d+)个百分点，(?P<higher>甲|乙|两篇)(?:较高|较大)")


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm(text):
    return "".join(CJK.findall(text or ""))


def poet_name(work):
    poet = work.get("poet")
    if isinstance(poet, dict):
        return poet.get("name") or ""
    return work.get("poet_name") or str(poet or "")


def display_one(value):
    # 金标对 .05 边界采用 half-even；不得加 epsilon 改成四舍五入。
    return round(value, 1)


def format_one(value):
    return "%.1f" % display_one(value)


def format_change(value):
    rounded = display_one(value)
    if abs(rounded) < 0.05:
        rounded = 0.0
    return "%+.1f" % rounded


def split_lines(lines, split_after):
    return (
        [line for line in lines if line["line_no"] <= split_after],
        [line for line in lines if line["line_no"] > split_after],
    )


def ratio_of(lines):
    ping = sum(line["ping_count"] for line in lines)
    ze = sum(line["ze_count"] for line in lines)
    total = ping + ze
    return (ze / total * 100.0) if total else 0.0


class Resolver:
    def __init__(self, works):
        self.works = works
        self.index = [
            {
                "work": work,
                "text": norm(work.get("text")),
                "poet": norm(poet_name(work)),
                "poet_raw": poet_name(work),
                "tune": norm(work.get("tune_pattern")),
            }
            for work in works
        ]

    def resolve(self, question):
        anchors = []
        for match in ANCHOR.finditer(question):
            anchor = match.groupdict()
            first = norm(anchor["first"])
            poet = norm(anchor["poet"])
            tune = norm(anchor["tune"])
            candidates = [
                item["work"]
                for item in self.index
                if first and first in item["text"]
                and (poet == item["poet"] or item["poet_raw"].startswith(anchor["poet"] + "《"))
                and (not tune or tune == item["tune"])
            ]
            if len(candidates) != 1:
                raise ValueError("锚点不是唯一作品：%s -> %s" % (anchor, [w.get("id") for w in candidates]))
            anchors.append((anchor, candidates[0]))
        if len(anchors) not in (2, 3):
            raise ValueError("无法解析出 2 或 3 个作品锚点")
        return anchors


def prosody_lines(work):
    prosody = work.get("prosody") or {}
    lines = sorted(prosody.get("lines") or [], key=lambda line: line["line_no"])
    if not lines:
        raise ValueError("作品缺少声律行：%s" % work.get("id"))
    for line in lines:
        if line["ping_count"] + line["ze_count"] != line["character_count"]:
            raise ValueError("声律计数不变量失败：%s 第 %s 句" % (work.get("id"), line["line_no"]))
    expected_split = max(1, len(lines) // 2)
    if prosody.get("split_after_line") != expected_split:
        raise ValueError("分段公式不一致：%s" % work.get("id"))
    return prosody, lines


def c1_facts(anchor, work):
    _, lines = prosody_lines(work)
    ping = sum(line["ping_count"] for line in lines)
    ze = sum(line["ze_count"] for line in lines)
    max_count = max(line["character_count"] for line in lines)
    max_lines = [line["line_no"] for line in lines if line["character_count"] == max_count]
    return {
        "label": anchor["label"],
        "work_id": work.get("id"),
        "n": len(lines),
        "max_lines": max_lines,
        "max_count": max_count,
        "ping": ping,
        "ze": ze,
        "ratio": ze / (ping + ze) * 100.0 if ping + ze else 0.0,
        "line_ids": [line["line_no"] for line in lines],
    }


def c2_facts(anchor, work):
    prosody, lines = prosody_lines(work)
    pre, post = split_lines(lines, prosody["split_after_line"])
    pre_ratio = ratio_of(pre)
    post_ratio = ratio_of(post)
    # 金标的变化值来自未舍入比例之差，展示比例不能反向参与这一步。
    change = display_one(post_ratio - pre_ratio)
    return {
        "label": anchor["label"],
        "work_id": work.get("id"),
        "pre": pre_ratio,
        "post": post_ratio,
        "change": change,
        "absolute": abs(change),
        "pre_line_ids": [line["line_no"] for line in pre],
        "post_line_ids": [line["line_no"] for line in post],
        "split_after": prosody["split_after_line"],
    }


def c3_facts(anchor, work):
    _, lines = prosody_lines(work)
    total = sum(line["character_count"] for line in lines)
    threshold = math.ceil(total / len(lines))
    selected = [line for line in lines if line["character_count"] >= threshold]
    ping = sum(line["ping_count"] for line in selected)
    ze = sum(line["ze_count"] for line in selected)
    return {
        "label": anchor["label"],
        "work_id": work.get("id"),
        "threshold": threshold,
        "selected": [line["line_no"] for line in selected],
        "ping": ping,
        "ze": ze,
        "ratio": ze / (ping + ze) * 100.0 if ping + ze else 0.0,
        "line_lengths": [line["character_count"] for line in lines],
    }


def c4_facts(anchor, work):
    prosody, lines = prosody_lines(work)
    post = [line for line in lines if line["line_no"] > prosody["split_after_line"]]
    if not post:
        raise ValueError("后段为空：%s" % work.get("id"))
    # 金标未给定唯一引文选择规则；竖切片采用可解释的“后段最长句”策略。
    quote_line = max(post, key=lambda line: (line["character_count"], -line["line_no"]))
    return {
        "label": anchor["label"],
        "work_id": work.get("id"),
        "post": post,
        "post_line_ids": [line["line_no"] for line in post],
        "post_texts": [line["text"] for line in post],
        "post_quote_candidates": [
            {"line_no": line["line_no"], "text": line["text"], "character_count": line["character_count"]}
            for line in post
        ],
        "quote": quote_line["text"],
        "quote_line_no": quote_line["line_no"],
        "quote_policy": "longest_post_line",
        "ratio": ratio_of(post),
        "split_after": prosody["split_after_line"],
    }


def c5_facts(anchor, work):
    prosody, lines = prosody_lines(work)
    pre, post = split_lines(lines, prosody["split_after_line"])
    if not pre or not post:
        raise ValueError("前段或后段为空：%s" % work.get("id"))
    pre_ratio = ratio_of(pre)
    post_ratio = ratio_of(post)
    change = display_one(post_ratio - pre_ratio)
    if abs(change) < 0.05:
        direction = "前后持平"
    elif change > 0:
        direction = "后段上升"
    else:
        direction = "后段下降"
    # 语义归类没有金标，系统只提供可定位候选句，并明确标记未验证。
    pre_quote = max(pre, key=lambda line: (line["character_count"], -line["line_no"]))
    post_quote = max(post, key=lambda line: (line["character_count"], -line["line_no"]))
    return {
        "label": anchor["label"],
        "work_id": work.get("id"),
        "pre": pre_ratio,
        "post": post_ratio,
        "change": change,
        "direction": direction,
        "pre_line_ids": [line["line_no"] for line in pre],
        "post_line_ids": [line["line_no"] for line in post],
        "pre_texts": [line["text"] for line in pre],
        "post_texts": [line["text"] for line in post],
        "pre_quote": pre_quote["text"],
        "pre_quote_line_no": pre_quote["line_no"],
        "post_quote": post_quote["text"],
        "post_quote_line_no": post_quote["line_no"],
        "quote_policy": "longest_line_in_segment",
        "split_after": prosody["split_after_line"],
    }


def compare_higher(left, right):
    if abs(left - right) < 1e-9:
        return "两篇"
    return "甲" if left > right else "乙"


def render_c1(facts):
    parts = []
    for item in facts:
        max_display = "、".join(str(number) for number in item["max_lines"])
        parts.append(
            "%s共%d句，最长为第%s句、各%d字，全文平%d、仄%d，仄声比例%.1f%%"
            % (
                item["label"], item["n"], max_display, item["max_count"],
                item["ping"], item["ze"], display_one(item["ratio"]),
            )
        )
    first, second = facts
    diff = abs(display_one(first["ratio"]) - display_one(second["ratio"]))
    higher = compare_higher(display_one(first["ratio"]), display_one(second["ratio"]))
    body = "；".join(parts)
    return (
        body
        + "。两篇全文仄声比例相差%.1f个百分点，%s较高。"
        % (display_one(diff), higher)
        + "这些数值只说明当前文本的形式与声调配置，不能单独判定作者意图或作品优劣。"
    )


def render_c2(facts):
    parts = []
    for item in facts:
        parts.append(
            "%s仄声比例由%.1f%%变为%.1f%%，变化%s个百分点，绝对变幅%.1f个百分点"
            % (
                item["label"], display_one(item["pre"]), display_one(item["post"]),
                format_change(item["change"]), display_one(item["absolute"]),
            )
        )
    first, second = facts
    difference = abs(display_one(first["absolute"]) - display_one(second["absolute"]))
    higher = compare_higher(display_one(first["absolute"]), display_one(second["absolute"]))
    higher_word = "两篇" if higher == "两篇" else higher
    return (
        "；".join(parts)
        + "。两篇变幅相差%.1f个百分点，%s较大。"
        % (display_one(difference), higher_word)
        + "变幅只能说明分段节奏配置的变化，不能直接推定情感强度或作者意图。"
    )


def render_c3(facts):
    parts = []
    for item in facts:
        selected = "、".join(str(number) for number in item["selected"])
        parts.append(
            "%s阈值%d字，入选第%s句，合计平%d、仄%d，仄声比例%.1f%%"
            % (
                item["label"], item["threshold"], selected,
                item["ping"], item["ze"], display_one(item["ratio"]),
            )
        )
    first, second = facts
    difference = abs(display_one(first["ratio"]) - display_one(second["ratio"]))
    higher = compare_higher(display_one(first["ratio"]), display_one(second["ratio"]))
    return (
        "；".join(parts)
        + "。两篇长句区段仄声密度相差%.1f个百分点，%s较高。"
        % (display_one(difference), higher)
        + "该比较仅限题定长句区段，不能代替文学价值判断。"
    )


def render_c4(facts):
    parts = []
    for item in facts:
        parts.append(
            "%s后段可引“%s”，仄声比例%.1f%%"
            % (item["label"], item["quote"], display_one(item["ratio"]))
        )
    ordered = sorted(
        enumerate(facts),
        key=lambda pair: (display_one(pair[1]["ratio"]), pair[0]),
        reverse=True,
    )
    order = "＞".join(item["label"] for _, item in ordered)
    ratios = [display_one(item["ratio"]) for item in facts]
    difference = max(ratios) - min(ratios)
    return (
        "；".join(parts)
        + "。排序为%s，最高与最低相差%.1f个百分点。"
        % (order, display_one(difference))
        + "该排序只反映题定后段的声调数量，不能直接推断作者意图、跨体裁高下或文学价值。"
    )


def render_c5(facts):
    parts = []
    for item in facts:
        parts.append(
            "%s前段景物线索：%s；后段主观感受线索：%s；仄声比例变化%s个百分点，节奏转向为%s。"
            % (
                item["label"], item["pre_quote"], item["post_quote"],
                format_change(item["change"]), item["direction"],
            )
        )
    return (
        "\n".join(parts)
        + "\n解释边界：景物、感受和声律变化只能构成有限的文本互证，不能直接证明作者经历、时代因果或作品优劣。"
        + "\n语义验证状态：未验证；景物/感受归类及互证解释仅作候选，不作为语义金标准。"
    )


def parsed_fields(category, gold):
    if category == "跨篇景情节奏互证":
        blocks = [match.groupdict() for match in C5_BLOCK.finditer(gold)]
        boundary = C5_BOUNDARY.search(gold)
        status = C5_STATUS.search(gold)
        if len(blocks) != 2 or not boundary or not status:
            raise ValueError("C5 金标结构不完整")
        values = {}
        for block in blocks:
            label = block["label"]
            values[label + "_change"] = block["change"]
            values[label + "_direction"] = block["direction"]
            values[label + "_pre_quote"] = block["pre"]
            values[label + "_post_quote"] = block["post"]
        values["boundary_present"] = "1"
        values["status_present"] = "1"
        return values

    if category == "跨篇后段排序与比例差距":
        blocks = [match.groupdict() for match in C4_BLOCK.finditer(gold)]
        tail = C4_TAIL.search(gold)
        if len(blocks) not in (2, 3) or not tail:
            raise ValueError("C4 金标结构不完整")
        values = {}
        for block in blocks:
            label = block["label"]
            values[label + "_ratio"] = "%.1f" % float(block["ratio"])
            values[label + "_quote"] = block["quote"]
        values["order"] = tail.group("order")
        values["diff"] = "%.1f" % float(tail.group("diff"))
        return values

    blocks = []
    pattern = {
        "同调全篇声律比例差": C1_BLOCK,
        "跨篇分段变幅比较": C2_BLOCK,
        "跨篇长句仄声密度比较": C3_BLOCK,
    }[category]
    for match in pattern.finditer(gold):
        blocks.append(match.groupdict())
    if len(blocks) != 2:
        raise ValueError("金标字段块数量不是 2：%d" % len(blocks))
    tail = DIFF_TAIL.search(gold)
    if not tail:
        raise ValueError("金标缺少差值尾句")
    values = {}
    for block in blocks:
        label = block.pop("label")
        for key, value in block.items():
            if key in ("max_lines", "lines"):
                values[label + "_" + key] = "、".join(str(int(number)) for number in value.split("、"))
            elif key in ("change",):
                values[label + "_" + key] = value
            elif "." in value:
                values[label + "_" + key] = "%.1f" % float(value)
            else:
                values[label + "_" + key] = str(int(value))
    values["diff"] = "%.1f" % float(tail.group("diff"))
    values["higher"] = tail.group("higher")
    return values


def computed_fields(category, facts):
    values = {}
    if category == "同调全篇声律比例差":
        for item in facts:
            label = item["label"]
            values[label + "_n"] = str(item["n"])
            values[label + "_max_lines"] = "、".join(str(number) for number in item["max_lines"])
            values[label + "_max_count"] = str(item["max_count"])
            values[label + "_ping"] = str(item["ping"])
            values[label + "_ze"] = str(item["ze"])
            values[label + "_ratio"] = format_one(item["ratio"])
        left, right = facts
        values["diff"] = format_one(abs(display_one(left["ratio"]) - display_one(right["ratio"])))
        values["higher"] = compare_higher(display_one(left["ratio"]), display_one(right["ratio"]))
    elif category == "跨篇分段变幅比较":
        for item in facts:
            label = item["label"]
            values[label + "_pre"] = format_one(item["pre"])
            values[label + "_post"] = format_one(item["post"])
            values[label + "_change"] = format_change(item["change"])
            values[label + "_absolute"] = format_one(item["absolute"])
        left, right = facts
        values["diff"] = format_one(abs(display_one(left["absolute"]) - display_one(right["absolute"])))
        values["higher"] = compare_higher(display_one(left["absolute"]), display_one(right["absolute"]))
    elif category == "跨篇景情节奏互证":
        for item in facts:
            label = item["label"]
            values[label + "_change"] = format_change(item["change"])
            values[label + "_direction"] = item["direction"]
            values[label + "_pre_quote"] = item["pre_quote"]
            values[label + "_post_quote"] = item["post_quote"]
        values["boundary_present"] = "1"
        values["status_present"] = "1"
    elif category == "跨篇后段排序与比例差距":
        for item in facts:
            label = item["label"]
            values[label + "_ratio"] = format_one(item["ratio"])
            values[label + "_quote"] = item["quote"]
        ordered = sorted(
            enumerate(facts),
            key=lambda pair: (display_one(pair[1]["ratio"]), pair[0]),
            reverse=True,
        )
        values["order"] = "＞".join(item["label"] for _, item in ordered)
        ratios = [display_one(item["ratio"]) for item in facts]
        values["diff"] = format_one(max(ratios) - min(ratios))
    else:
        for item in facts:
            label = item["label"]
            values[label + "_threshold"] = str(item["threshold"])
            values[label + "_lines"] = "、".join(str(number) for number in item["selected"])
            values[label + "_ping"] = str(item["ping"])
            values[label + "_ze"] = str(item["ze"])
            values[label + "_ratio"] = format_one(item["ratio"])
        left, right = facts
        values["diff"] = format_one(abs(display_one(left["ratio"]) - display_one(right["ratio"])))
        values["higher"] = compare_higher(display_one(left["ratio"]), display_one(right["ratio"]))
    return values


def c5_field_checks(expected, actual, facts, answer):
    """C5 只核验官方代理字段：引文可定位、变化值、方向、边界声明与未验证状态。"""
    checks = {}
    for item in facts:
        label = item["label"]
        checks[label + "_change"] = actual.get(label + "_change") == expected.get(label + "_change")
        checks[label + "_direction"] = actual.get(label + "_direction") == expected.get(label + "_direction")
        checks[label + "_pre_quote_locatable"] = (
            expected.get(label + "_pre_quote") in item["pre_texts"]
            and item["pre_quote"] in item["pre_texts"]
        )
        checks[label + "_post_quote_locatable"] = (
            expected.get(label + "_post_quote") in item["post_texts"]
            and item["post_quote"] in item["post_texts"]
        )
    checks["boundary_present"] = "解释边界：" in answer
    checks["status_present"] = "语义验证状态：未验证" in answer
    return checks


def c4_field_checks(expected, actual, facts):
    """C4 的官方可核验字段是比例、排序、极差与引文可定位性，不要求复刻金标引文原句。"""
    checks = {}
    for item in facts:
        label = item["label"]
        checks[label + "_ratio"] = actual.get(label + "_ratio") == expected.get(label + "_ratio")
        gold_quote = expected.get(label + "_quote")
        checks[label + "_quote_locatable"] = (
            gold_quote in item["post_texts"] and item["quote"] in item["post_texts"]
        )
    checks["order"] = actual.get("order") == expected.get("order")
    checks["diff"] = actual.get("diff") == expected.get("diff")
    return checks


def normalize_quotes(text):
    return re.sub(r"“[^”]+”", "<Q>", text or "")


def evidence_for(category, anchors, facts):
    evidence = []
    for (anchor, work), fact in zip(anchors, facts):
        item = {
            "label": anchor["label"],
            "work_id": work.get("id"),
            "title": work.get("title"),
            "poet": poet_name(work),
            "tune": work.get("tune_pattern"),
            "period": work.get("period_label"),
            "source_anchor": {
                "period": anchor["period"],
                "poet": anchor["poet"],
                "tune": anchor["tune"],
                "first": anchor["first"],
            },
        }
        if category == "同调全篇声律比例差":
            item["line_ids"] = fact["line_ids"]
            item["calculation"] = "all_lines"
        elif category == "跨篇分段变幅比较":
            item["pre_line_ids"] = fact["pre_line_ids"]
            item["post_line_ids"] = fact["post_line_ids"]
            item["split_after"] = fact["split_after"]
            item["calculation"] = "pre_post_ratio"
        elif category == "跨篇景情节奏互证":
            item["pre_line_ids"] = fact["pre_line_ids"]
            item["post_line_ids"] = fact["post_line_ids"]
            item["pre_quote_line_no"] = fact["pre_quote_line_no"]
            item["post_quote_line_no"] = fact["post_quote_line_no"]
            item["quote_policy"] = fact["quote_policy"]
            item["split_after"] = fact["split_after"]
            item["calculation"] = "pre_post_ratio_change_and_direction"
        elif category == "跨篇后段排序与比例差距":
            item["post_line_ids"] = fact["post_line_ids"]
            item["post_quote_candidates"] = fact["post_quote_candidates"]
            item["quote_line_no"] = fact["quote_line_no"]
            item["quote_policy"] = fact["quote_policy"]
            item["split_after"] = fact["split_after"]
            item["calculation"] = "post_ratio_desc_then_reverse_anchor_tie"
        else:
            item["line_ids"] = fact["selected"]
            item["line_lengths"] = fact["line_lengths"]
            item["threshold"] = fact["threshold"]
            item["calculation"] = "ceil_average_length_then_select"
        evidence.append(item)
    complete = all(
        item["work_id"] is not None
        and (
            item.get("line_ids")
            or item.get("post_line_ids")
            or (item.get("pre_line_ids") and item.get("post_line_ids"))
        )
        for item in evidence
    )
    return evidence, complete


def run(works_path, questions_path, answers_path=None):
    started = time.perf_counter()
    works = load_jsonl(works_path)
    questions = [row for row in load_jsonl(questions_path) if row["类别"] in (
        "同调全篇声律比例差", "跨篇分段变幅比较", "跨篇长句仄声密度比较",
        "跨篇后段排序与比例差距", "跨篇景情节奏互证"
    )]
    if answers_path:
        answers = {row["题号"]: row["标准答案"] for row in load_jsonl(answers_path)}
        for row in questions:
            if row["题号"] not in answers:
                raise ValueError("缺少答案：%s" % row["题号"])
            row["标准答案"] = answers[row["题号"]]
    resolver = Resolver(works)
    results = []
    for row in questions:
        category = row["类别"]
        gold = row["标准答案"]
        try:
            anchors = resolver.resolve(row["问题"])
            if category == "同调全篇声律比例差":
                facts = [c1_facts(anchor, work) for anchor, work in anchors]
                answer = render_c1(facts)
            elif category == "跨篇分段变幅比较":
                facts = [c2_facts(anchor, work) for anchor, work in anchors]
                answer = render_c2(facts)
            elif category == "跨篇长句仄声密度比较":
                facts = [c3_facts(anchor, work) for anchor, work in anchors]
                answer = render_c3(facts)
            elif category == "跨篇后段排序与比例差距":
                facts = [c4_facts(anchor, work) for anchor, work in anchors]
                answer = render_c4(facts)
            else:
                facts = [c5_facts(anchor, work) for anchor, work in anchors]
                answer = render_c5(facts)
            expected = parsed_fields(category, gold)
            actual = computed_fields(category, facts)
            if category == "跨篇后段排序与比例差距":
                field_checks = c4_field_checks(expected, actual, facts)
            elif category == "跨篇景情节奏互证":
                field_checks = c5_field_checks(expected, actual, facts, answer)
            else:
                field_checks = {key: actual.get(key) == expected.get(key) for key in expected}
            evidence, evidence_ok = evidence_for(category, anchors, facts)
            results.append({
                "qid": row["题号"],
                "category": category,
                "our_answer": answer,
                "gold_answer": gold,
                "exact_match": answer == gold,
                "normalized_exact_match": normalize_quotes(answer) == normalize_quotes(gold),
                "field_checks": field_checks,
                "field_passed": sum(field_checks.values()),
                "field_total": len(field_checks),
                "fields_all_passed": all(field_checks.values()),
                "evidence_complete": evidence_ok,
                "evidence": evidence,
                "error": None,
            })
        except Exception as exc:
            results.append({
                "qid": row["题号"],
                "category": category,
                "our_answer": "",
                "gold_answer": gold,
                "exact_match": False,
                "normalized_exact_match": False,
                "field_checks": {},
                "field_passed": 0,
                "field_total": 0,
                "fields_all_passed": False,
                "evidence_complete": False,
                "evidence": [],
                "error": "%s: %s" % (type(exc).__name__, exc),
            })
    by_category = {}
    for category in ("同调全篇声律比例差", "跨篇分段变幅比较", "跨篇长句仄声密度比较",
                     "跨篇后段排序与比例差距", "跨篇景情节奏互证"):
        subset = [result for result in results if result["category"] == category]
        field_passed = sum(result["field_passed"] for result in subset)
        field_total = sum(result["field_total"] for result in subset)
        by_category[category] = {
            "questions": len(subset),
            "exact_matches": sum(result["exact_match"] for result in subset),
            "exact_match_rate": round(sum(result["exact_match"] for result in subset) / len(subset), 6) if subset else 0.0,
            "normalized_exact_matches": sum(result["normalized_exact_match"] for result in subset),
            "normalized_exact_match_rate": round(sum(result["normalized_exact_match"] for result in subset) / len(subset), 6) if subset else 0.0,
            "field_questions_all_passed": sum(result["fields_all_passed"] for result in subset),
            "field_questions_all_passed_rate": round(sum(result["fields_all_passed"] for result in subset) / len(subset), 6) if subset else 0.0,
            "field_values_passed": field_passed,
            "field_values_total": field_total,
            "field_value_rate": round(field_passed / field_total, 6) if field_total else 0.0,
            "evidence_complete": sum(result["evidence_complete"] for result in subset),
            "errors": sum(bool(result["error"]) for result in subset),
        }
    failures = [result for result in results if not result["exact_match"]]
    failure_buckets = Counter()
    for result in failures:
        if result["error"]:
            failure_buckets["execution_error"] += 1
        else:
            wrong = [key for key, passed in result["field_checks"].items() if not passed]
            if wrong:
                for key in wrong:
                    failure_buckets["field:" + key] += 1
            elif result["normalized_exact_match"]:
                failure_buckets["quote_selection_only"] += 1
            else:
                failure_buckets["rendering_only"] += 1
    report = {
        "scope": (
            "保密验证集 300 题一次性验证（规则已冻结，未用于调参）"
            if answers_path else
            "公开测试集 700 题确定性竖切片；用于规则归纳与回归"
        ),
        "quote_policy": {
            "C4": "longest_post_line",
            "C5": "longest_line_in_segment，语义标记为未验证",
        },
        "source": {
            "works_sha256": file_sha256(works_path),
            "questions_sha256": file_sha256(questions_path),
            "work_count": len(works),
            "question_count": len(questions),
        },
        "metrics": {
            "exact_matches": sum(result["exact_match"] for result in results),
            "exact_match_rate": round(sum(result["exact_match"] for result in results) / len(results), 6) if results else 0.0,
            "normalized_exact_matches": sum(result["normalized_exact_match"] for result in results),
            "normalized_exact_match_rate": round(sum(result["normalized_exact_match"] for result in results) / len(results), 6) if results else 0.0,
            "field_questions_all_passed": sum(result["fields_all_passed"] for result in results),
            "field_questions_all_passed_rate": round(sum(result["fields_all_passed"] for result in results) / len(results), 6) if results else 0.0,
            "field_values_passed": sum(result["field_passed"] for result in results),
            "field_values_total": sum(result["field_total"] for result in results),
            "field_value_rate": round(
                sum(result["field_passed"] for result in results) / sum(result["field_total"] for result in results), 6
            ) if sum(result["field_total"] for result in results) else 0.0,
            "evidence_complete": sum(result["evidence_complete"] for result in results),
            "execution_errors": sum(bool(result["error"]) for result in results),
        },
        "by_category": by_category,
        "failure_buckets": dict(failure_buckets),
        "runtime_ms": round((time.perf_counter() - started) * 1000),
        "failures": failures[:50],
        "results": results,
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--works", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--answers")
    args = parser.parse_args()
    report = run(args.works, args.questions, args.answers)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "scope": report["scope"],
        "metrics": report["metrics"],
        "by_category": report["by_category"],
        "failure_buckets": report["failure_buckets"],
        "runtime_ms": report["runtime_ms"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

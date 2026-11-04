"""确定性声律引擎：五类题的求解、渲染与证据链。

约束（ADR-0001）：本模块不调用任何模型。所有数值来自 Tier 1 官方逐句标注，
输出可逐项复算；无法判定时抛 ApiError，不返回近似值。
"""

import math
import re
import threading
import uuid

from . import qingci, rules
from .errors import ApiError

CJK = "\u3400-\u9fff"
# 题面锚点的写法。评测集「参考题库、词曲可能不一样」——换作品时题面格式
# 也可能有细微差别（引号样式、括号全半角、词牌带不带书名号、首句末尾标点）。
# 这里把常见写法都容下：**宁可多解析几种写法，也不要因为排版差异整题拒答**。
# 正式题库（1000 题）实测 100% 使用「（词牌“X”，首句“Y”）。」这一种。
_QUOTE = "“”‘’「」『』\"'"
_LABEL = "甲乙丙ＡＢＣABC①②③"

ANCHOR = re.compile(
    r"(?P<label>[" + _LABEL + r"])\s*[:：]\s*"
    r"(?P<period>[^·・\n]{1,12})[·・]\s*"
    r"(?P<poet>[^《（(【\[\n]{1,24})\s*[《（(【\[]\s*"
    # title_block 用 `.`（除换行外都能吃）：作品标题里可能嵌套书名号，
    # 例如《谒金门·寄汉槎兄塞外见《今词初集》、《瑶华集》…》——
    # 用排除式字符类会卡在第一个「》」前，过不去锚点。
    # 至少 1 字：题面里同一首词可能出现两次「首句」，空标题会选错锚点。
    r"(?P<title_block>.{1,120}?)\s*[》）)】]?\s*"
    r"[（(【\[]?\s*词牌(?:名)?\s*[《【\[]?[" + _QUOTE + r"]?\s*"
    r"(?P<tune>[^》】" + _QUOTE + r"《【\[\]\n（），,、；;：:]{1,24}?)\s*[》】\]]?\s*[" + _QUOTE + r"]?\s*"
    r"[，,、]\s*首句\s*[" + _QUOTE + r"]\s*"
    r"(?P<first>[^" + _QUOTE + r"\n]{1,80}?)"
    r"(?:[" + _QUOTE + r"]|\s*[。．.]?\s*[）)】]|\s*$)"
)

# 类别名、判类措辞、C5 边界文本与闸名词表都从 rules.json 读（V6.1 §1.7 规则外置）。
# 文件缺项会在 import 阶段明确报错，不静默退回内置默认值。
(
    CATEGORY_C1,
    CATEGORY_C2,
    CATEGORY_C3,
    CATEGORY_C4,
    CATEGORY_C5,
) = rules.category_names()

IMPLEMENTED = list(rules.category_names())

C5_BOUNDARY, C5_STATUS = rules.c5_text()


def display_one(value: float) -> float:
    """金标在 .05 边界用 half-even；加 epsilon 会系统性改变结果。"""
    return round(value, 1)


def format_one(value: float) -> str:
    return "%.1f" % display_one(value)


def format_change(value: float) -> str:
    rounded = display_one(value)
    if abs(rounded) < 0.05:
        rounded = 0.0
    return "%+.1f" % rounded


def _ratio_of(lines: list[dict]) -> float:
    ping = sum(line["ping_count"] for line in lines)
    ze = sum(line["ze_count"] for line in lines)
    total = ping + ze
    return (ze / total * 100.0) if total else 0.0


def _compare(left: float, right: float) -> str:
    if abs(left - right) < 1e-9:
        return "两篇"
    return "甲" if left > right else "乙"


def _prosody_lines(work: dict) -> tuple[dict, list[dict]]:
    prosody = work.get("prosody") or {}
    lines = sorted(prosody.get("lines") or [], key=lambda line: line["line_no"])
    if not lines:
        raise ApiError("INSUFFICIENT_DATA", "作品缺少逐句声律", {"work_id": work.get("id")})
    for line in lines:
        if line["ping_count"] + line["ze_count"] != line["character_count"]:
            raise ApiError(
                "INSUFFICIENT_DATA",
                "声律计数不变量失败，拒绝输出",
                {"work_id": work.get("id"), "line_no": line["line_no"]},
            )
    if prosody.get("split_after_line") != max(1, len(lines) // 2):
        raise ApiError(
            "INSUFFICIENT_DATA",
            "分段公式与官方口径不一致，拒绝输出",
            {"work_id": work.get("id"), "split_after_line": prosody.get("split_after_line")},
        )
    return prosody, lines


def _split(lines: list[dict], split_after: int) -> tuple[list[dict], list[dict]]:
    return (
        [line for line in lines if line["line_no"] <= split_after],
        [line for line in lines if line["line_no"] > split_after],
    )


def _poet_of(work: dict) -> str:
    poet = work.get("poet")
    if isinstance(poet, dict):
        return poet.get("name") or ""
    return work.get("poet_name") or ""


def _base_fact(anchor: dict, work: dict) -> dict:
    # 来源逐篇区分：本地快照命中的标 Tier 1，官方 API 实时兜底的标 API
    source = work.get("_source") or "Tier 1"
    return {
        "label": anchor["label"],
        "work_id": work.get("id"),
        "title": work.get("title"),
        "poet": _poet_of(work),
        "source": source,
        "tier_label": "官方标注（API 实时查询）" if source != "Tier 1" else "官方标注",
    }


def _evidence(fact: dict, line_ids: list[int], **extra) -> dict:
    item = {
        "work_id": fact["work_id"],
        "title": fact["title"],
        "poet": fact["poet"],
        "line_ids": line_ids,
        "source": fact["source"],
        "tier_label": fact["tier_label"],
    }
    item.update(extra)
    return item


def _quote_candidates(lines: list[dict], limit: int = 3) -> list[dict]:
    """段内候选引文：按句长降序、同长取句序靠前。

    只是"可定位候选"，不声称语义归类——官方判分也只核验能否定位到该段。
    给出多条是为了让复核者在同一段里换一条更贴题的句子，而不必怀疑数据。
    """
    ranked = sorted(lines, key=lambda line: (-line["character_count"], line["line_no"]))
    return [{"line_no": line["line_no"], "text": line["text"]} for line in ranked[:limit]]


def detect_category(question: str) -> str:
    """按题面措辞判类；关键词与顺序都在 rules.json 里，措辞漂移时可热修。"""
    category = rules.detect(question)
    if category:
        return category
    raise ApiError(
        "INVALID_INPUT",
        "题面无法识别为已知类别",
        {"hint": "需包含作品锚点与题面规则", "implemented": IMPLEMENTED},
    )


def parse_anchors(question: str) -> list[dict]:
    anchors = [match.groupdict() for match in ANCHOR.finditer(question)]
    if len(anchors) not in (2, 3):
        raise ApiError("INVALID_INPUT", "题面作品锚点数量应为 2 或 3 个", {"found": len(anchors)})
    return anchors


def solve_c1(anchors: list[dict], works: list[dict]) -> dict:
    facts = []
    for anchor, work in zip(anchors, works):
        _, lines = _prosody_lines(work)
        max_count = max(line["character_count"] for line in lines)
        fact = _base_fact(anchor, work)
        fact.update(
            {
                "n": len(lines),
                "max_lines": [line["line_no"] for line in lines if line["character_count"] == max_count],
                "max_count": max_count,
                "ping": sum(line["ping_count"] for line in lines),
                "ze": sum(line["ze_count"] for line in lines),
                "ratio": _ratio_of(lines),
                "line_ids": [line["line_no"] for line in lines],
            }
        )
        facts.append(fact)

    left, right = facts[0], facts[1]
    left_ratio, right_ratio = display_one(left["ratio"]), display_one(right["ratio"])
    diff = display_one(abs(left_ratio - right_ratio))
    higher = _compare(left_ratio, right_ratio)

    answer = (
        "；".join(
            "%s共%d句，最长为第%s句、各%d字，全文平%d、仄%d，仄声比例%s%%"
            % (
                item["label"], item["n"], "、".join(str(n) for n in item["max_lines"]),
                item["max_count"], item["ping"], item["ze"], format_one(item["ratio"]),
            )
            for item in facts
        )
        + "。两篇全文仄声比例相差%s个百分点，%s较高。" % (format_one(diff), higher)
        + "这些数值只说明当前文本的形式与声调配置，不能单独判定作者意图或作品优劣。"
    )

    fields: dict = {}
    for item in facts:
        prefix = item["label"]
        fields.update(
            {
                prefix + "_n": item["n"],
                prefix + "_max_lines": item["max_lines"],
                prefix + "_max_count": item["max_count"],
                prefix + "_ping": item["ping"],
                prefix + "_ze": item["ze"],
                prefix + "_ratio": format_one(item["ratio"]),
            }
        )
    fields.update({"diff": format_one(diff), "higher": higher})

    trace = [
        {
            "step": "取逐句声律",
            "input": "Tier 1 prosody.lines",
            "formula": "句数 = len(lines)",
            "output": {item["label"]: item["n"] for item in facts},
            "evidence_ids": ["w%s:L1-L%d" % (item["work_id"], item["line_ids"][-1]) for item in facts],
        },
        {
            "step": "找最长句",
            "input": "逐句 character_count",
            "formula": "max(character_count)，并列保留全部句序",
            "output": {item["label"]: item["max_lines"] for item in facts},
            "evidence_ids": ["w%s:chars" % item["work_id"] for item in facts],
        },
        {
            "step": "合计平仄并求比例",
            "input": "逐句 ping_count / ze_count",
            "formula": "ze / (ping + ze) × 100，保留一位（half-even）",
            "output": {item["label"]: format_one(item["ratio"]) for item in facts},
            "evidence_ids": ["w%s:sum" % item["work_id"] for item in facts],
        },
        {
            "step": "比较并求差",
            "input": "两篇展示比例",
            "formula": "|甲 − 乙|，按展示值相减",
            "output": {"diff": format_one(diff), "higher": higher},
            "evidence_ids": ["calc:ratio-diff"],
        },
    ]
    return {
        "category": CATEGORY_C1,
        "answer": answer,
        "fields": fields,
        "trace": trace,
        "evidence": [_evidence(item, item["line_ids"]) for item in facts],
    }


def solve_c2(anchors: list[dict], works: list[dict]) -> dict:
    facts = []
    for anchor, work in zip(anchors, works):
        prosody, lines = _prosody_lines(work)
        pre, post = _split(lines, prosody["split_after_line"])
        pre_ratio, post_ratio = _ratio_of(pre), _ratio_of(post)
        change = display_one(post_ratio - pre_ratio)
        fact = _base_fact(anchor, work)
        fact.update(
            {
                "pre": pre_ratio,
                "post": post_ratio,
                "change": change,
                "absolute": abs(change),
                "pre_line_ids": [line["line_no"] for line in pre],
                "post_line_ids": [line["line_no"] for line in post],
                "split_after": prosody["split_after_line"],
            }
        )
        facts.append(fact)

    first, second = facts[0], facts[1]
    difference = display_one(abs(display_one(first["absolute"]) - display_one(second["absolute"])))
    higher = _compare(display_one(first["absolute"]), display_one(second["absolute"]))

    answer = (
        "；".join(
            "%s仄声比例由%s%%变为%s%%，变化%s个百分点，绝对变幅%s个百分点"
            % (
                item["label"], format_one(item["pre"]), format_one(item["post"]),
                format_change(item["change"]), format_one(item["absolute"]),
            )
            for item in facts
        )
        + "。两篇变幅相差%s个百分点，%s较大。" % (format_one(difference), higher)
        + "变幅只能说明分段节奏配置的变化，不能直接推定情感强度或作者意图。"
    )

    fields: dict = {"diff": format_one(difference), "higher": higher}
    for item in facts:
        prefix = item["label"]
        fields.update(
            {
                prefix + "_pre": format_one(item["pre"]),
                prefix + "_post": format_one(item["post"]),
                prefix + "_change": format_change(item["change"]),
                prefix + "_absolute": format_one(item["absolute"]),
            }
        )

    trace = [
        {
            "step": "按官方分段切前后段",
            "input": "split_after_line",
            "formula": "line_no ≤ split_after_line 为前段，其余为后段",
            "output": {item["label"]: [len(item["pre_line_ids"]), len(item["post_line_ids"])] for item in facts},
            "evidence_ids": ["w%s:split" % item["work_id"] for item in facts],
        },
        {
            "step": "算前后段比例",
            "input": "各段 ping_count / ze_count 合计",
            "formula": "ze / (ping + ze) × 100",
            "output": {item["label"]: [format_one(item["pre"]), format_one(item["post"])] for item in facts},
            "evidence_ids": ["w%s:segment-ratio" % item["work_id"] for item in facts],
        },
        {
            "step": "求变化值与绝对变幅",
            "input": "未舍入的前后段比例",
            "formula": "change = post − pre（未舍入相减后再保留一位）；absolute = |change|",
            "output": {item["label"]: format_change(item["change"]) for item in facts},
            "evidence_ids": ["calc:change"],
        },
        {
            "step": "比较变幅并求差",
            "input": "两篇绝对变幅",
            "formula": "|甲 − 乙|（展示值）",
            "output": {"diff": format_one(difference), "higher": higher},
            "evidence_ids": ["calc:absolute-diff"],
        },
    ]
    return {
        "category": CATEGORY_C2,
        "answer": answer,
        "fields": fields,
        "trace": trace,
        "evidence": [
            _evidence(item, item["pre_line_ids"] + item["post_line_ids"], split_after=item["split_after"])
            for item in facts
        ],
    }


def solve_c3(anchors: list[dict], works: list[dict]) -> dict:
    facts = []
    for anchor, work in zip(anchors, works):
        _, lines = _prosody_lines(work)
        threshold = math.ceil(sum(line["character_count"] for line in lines) / len(lines))
        selected = [line for line in lines if line["character_count"] >= threshold]
        fact = _base_fact(anchor, work)
        fact.update(
            {
                "threshold": threshold,
                "selected": [line["line_no"] for line in selected],
                "ping": sum(line["ping_count"] for line in selected),
                "ze": sum(line["ze_count"] for line in selected),
                "ratio": _ratio_of(selected),
                "line_lengths": [line["character_count"] for line in lines],
                "line_ids": [line["line_no"] for line in lines],
            }
        )
        facts.append(fact)

    first, second = facts[0], facts[1]
    difference = display_one(abs(display_one(first["ratio"]) - display_one(second["ratio"])))
    higher = _compare(display_one(first["ratio"]), display_one(second["ratio"]))

    answer = (
        "；".join(
            "%s阈值%d字，入选第%s句，合计平%d、仄%d，仄声比例%s%%"
            % (
                item["label"], item["threshold"], "、".join(str(n) for n in item["selected"]),
                item["ping"], item["ze"], format_one(item["ratio"]),
            )
            for item in facts
        )
        + "。两篇长句区段仄声密度相差%s个百分点，%s较高。" % (format_one(difference), higher)
        + "该比较仅限题定长句区段，不能代替文学价值判断。"
    )

    fields: dict = {"diff": format_one(difference), "higher": higher}
    for item in facts:
        prefix = item["label"]
        fields.update(
            {
                prefix + "_threshold": item["threshold"],
                prefix + "_selected": item["selected"],
                prefix + "_ping": item["ping"],
                prefix + "_ze": item["ze"],
                prefix + "_ratio": format_one(item["ratio"]),
            }
        )

    trace = [
        {
            "step": "求长句阈值",
            "input": "全篇总字数与句数",
            "formula": "ceil(总字数 / 句数)",
            "output": {item["label"]: item["threshold"] for item in facts},
            "evidence_ids": ["w%s:lengths" % item["work_id"] for item in facts],
        },
        {
            "step": "筛出入选句",
            "input": "逐句 character_count 与阈值",
            "formula": "character_count ≥ 阈值",
            "output": {item["label"]: item["selected"] for item in facts},
            "evidence_ids": ["w%s:selected" % item["work_id"] for item in facts],
        },
        {
            "step": "合计入选句平仄并求比例",
            "input": "入选句 ping_count / ze_count",
            "formula": "ze / (ping + ze) × 100，保留一位",
            "output": {item["label"]: format_one(item["ratio"]) for item in facts},
            "evidence_ids": ["w%s:selected-sum" % item["work_id"] for item in facts],
        },
        {
            "step": "求密度差",
            "input": "两篇入选区段展示比例",
            "formula": "|甲 − 乙|",
            "output": {"diff": format_one(difference), "higher": higher},
            "evidence_ids": ["calc:density-diff"],
        },
    ]
    return {
        "category": CATEGORY_C3,
        "answer": answer,
        "fields": fields,
        "trace": trace,
        "evidence": [_evidence(item, item["selected"], threshold=item["threshold"]) for item in facts],
    }


def solve_c4(anchors: list[dict], works: list[dict]) -> dict:
    facts = []
    for anchor, work in zip(anchors, works):
        prosody, lines = _prosody_lines(work)
        post = [line for line in lines if line["line_no"] > prosody["split_after_line"]]
        if not post:
            raise ApiError("INSUFFICIENT_DATA", "后段为空", {"work_id": work.get("id")})
        quote_line = max(post, key=lambda line: (line["character_count"], -line["line_no"]))
        fact = _base_fact(anchor, work)
        fact.update(
            {
                "quote": quote_line["text"],
                "quote_line_no": quote_line["line_no"],
                "ratio": _ratio_of(post),
                "post_line_ids": [line["line_no"] for line in post],
                "candidates": [
                    {"line_no": line["line_no"], "text": line["text"]}
                    for line in sorted(post, key=lambda line: (-line["character_count"], line["line_no"]))
                ],
                "split_after": prosody["split_after_line"],
            }
        )
        facts.append(fact)

    ordered = sorted(
        enumerate(facts),
        key=lambda pair: (display_one(pair[1]["ratio"]), pair[0]),
        reverse=True,
    )
    order = "＞".join(item["label"] for _, item in ordered)
    ratios = [display_one(item["ratio"]) for item in facts]
    difference = display_one(max(ratios) - min(ratios))

    answer = (
        "；".join(
            "%s后段可引“%s”，仄声比例%s%%" % (item["label"], item["quote"], format_one(item["ratio"]))
            for item in facts
        )
        + "。排序为%s，最高与最低相差%s个百分点。" % (order, format_one(difference))
        + "该排序只反映题定后段的声调数量，不能直接推断作者意图、跨体裁高下或文学价值。"
    )

    fields: dict = {"order": order, "diff": format_one(difference)}
    for item in facts:
        fields[item["label"] + "_ratio"] = format_one(item["ratio"])
        fields[item["label"] + "_quote"] = item["quote"]

    trace = [
        {
            "step": "取后段句集",
            "input": "split_after_line",
            "formula": "line_no > split_after_line",
            "output": {item["label"]: item["post_line_ids"] for item in facts},
            "evidence_ids": ["w%s:post" % item["work_id"] for item in facts],
        },
        {
            "step": "选可引句并求后段比例",
            "input": "后段逐句 character_count / ping / ze",
            "formula": "引文取后段最长句（并列取句序靠前者）；ratio = ze/(ping+ze)×100",
            "output": {item["label"]: format_one(item["ratio"]) for item in facts},
            "evidence_ids": ["w%s:quote" % item["work_id"] for item in facts],
        },
        {
            "step": "排序",
            "input": "各篇展示比例",
            "formula": "降序；等比例时题面后出现的锚点在前",
            "output": {"order": order},
            "evidence_ids": ["calc:order"],
        },
        {
            "step": "求极差",
            "input": "展示比例最大与最小",
            "formula": "max − min",
            "output": {"diff": format_one(difference)},
            "evidence_ids": ["calc:spread"],
        },
    ]
    return {
        "category": CATEGORY_C4,
        "answer": answer,
        "fields": fields,
        "trace": trace,
        "evidence": [
            _evidence(
                item,
                item["post_line_ids"],
                quote=item["quote"],
                quote_line_no=item["quote_line_no"],
                candidates=item["candidates"],
            )
            for item in facts
        ],
    }


def solve_c5(anchors: list[dict], works: list[dict]) -> dict:
    facts = []
    for anchor, work in zip(anchors, works):
        prosody, lines = _prosody_lines(work)
        pre, post = _split(lines, prosody["split_after_line"])
        if not pre or not post:
            raise ApiError("INSUFFICIENT_DATA", "前段或后段为空", {"work_id": work.get("id")})
        pre_ratio, post_ratio = _ratio_of(pre), _ratio_of(post)
        change = display_one(post_ratio - pre_ratio)
        if abs(change) < 0.05:
            direction = "前后持平"
        elif change > 0:
            direction = "后段上升"
        else:
            direction = "后段下降"
        pre_quote = max(pre, key=lambda line: (line["character_count"], -line["line_no"]))
        post_quote = max(post, key=lambda line: (line["character_count"], -line["line_no"]))
        fact = _base_fact(anchor, work)
        fact.update(
            {
                "pre": pre_ratio,
                "post": post_ratio,
                "change": change,
                "direction": direction,
                "pre_line_ids": [line["line_no"] for line in pre],
                "post_line_ids": [line["line_no"] for line in post],
                "pre_quote": pre_quote["text"],
                "pre_quote_line_no": pre_quote["line_no"],
                "post_quote": post_quote["text"],
                "post_quote_line_no": post_quote["line_no"],
                "pre_candidates": _quote_candidates(pre),
                "post_candidates": _quote_candidates(post),
                "quote_policy": "longest_line_in_segment",
                "split_after": prosody["split_after_line"],
            }
        )
        facts.append(fact)

    answer = (
        "\n".join(
            "%s前段景物线索：%s；后段主观感受线索：%s；仄声比例变化%s个百分点，节奏转向为%s。"
            % (
                item["label"], item["pre_quote"], item["post_quote"],
                format_change(item["change"]), item["direction"],
            )
            for item in facts
        )
        + "\n"
        + C5_BOUNDARY
        + "\n"
        + C5_STATUS
    )

    fields: dict = {"boundary_present": "1", "status_present": "1"}
    for item in facts:
        prefix = item["label"]
        fields.update(
            {
                prefix + "_change": format_change(item["change"]),
                prefix + "_direction": item["direction"],
                prefix + "_pre_quote": item["pre_quote"],
                prefix + "_post_quote": item["post_quote"],
            }
        )

    trace = [
        {
            "step": "按官方分段切前后段",
            "input": "split_after_line",
            "formula": "line_no ≤ split_after_line 为前段",
            "output": {item["label"]: [len(item["pre_line_ids"]), len(item["post_line_ids"])] for item in facts},
            "evidence_ids": ["w%s:split" % item["work_id"] for item in facts],
        },
        {
            "step": "选候选引文",
            "input": "前后段逐句 character_count",
            "formula": "各段取最长句（并列取句序靠前者）为默认引文，另附段内最长 3 句作可定位候选；均为候选，不代表语义归类",
            "output": {item["label"]: [item["pre_quote_line_no"], item["post_quote_line_no"]] for item in facts},
            "evidence_ids": ["w%s:quote-candidates" % item["work_id"] for item in facts],
        },
        {
            "step": "算比例变化与方向",
            "input": "未舍入的前后段比例",
            "formula": "change = post − pre；>0 后段上升，<0 后段下降，|change|<0.05 持平",
            "output": {item["label"]: format_change(item["change"]) for item in facts},
            "evidence_ids": ["calc:c5-change"],
        },
        {
            "step": "标注语义状态",
            "input": "—",
            "formula": "语义归类与互证解释无金标，固定标未验证",
            "output": {"verification": "未验证"},
            "evidence_ids": ["policy:adr-0005"],
        },
    ]
    return {
        "category": CATEGORY_C5,
        "answer": answer,
        "fields": fields,
        "trace": trace,
        "evidence": [
            _evidence(
                item,
                item["pre_line_ids"] + item["post_line_ids"],
                segments=[
                    {
                        "segment": "前段",
                        "quote": item["pre_quote"],
                        "quote_line_no": item["pre_quote_line_no"],
                        "candidates": item["pre_candidates"],
                    },
                    {
                        "segment": "后段",
                        "quote": item["post_quote"],
                        "quote_line_no": item["post_quote_line_no"],
                        "candidates": item["post_candidates"],
                    },
                ],
                quote_policy=item["quote_policy"],
            )
            for item in facts
        ],
    }


SOLVERS = {
    CATEGORY_C1: solve_c1,
    CATEGORY_C2: solve_c2,
    CATEGORY_C3: solve_c3,
    CATEGORY_C4: solve_c4,
    CATEGORY_C5: solve_c5,
}


# 最近若干次求解的留档，供 /api/solve/{id}/trace 单独取推理链（契约 §3.2）。
SOLVES: dict = {}
SOLVE_LOCK = threading.Lock()
MAX_SOLVES = 50


def _register(result: dict) -> str:
    solve_id = "solve_" + uuid.uuid4().hex[:12]
    with SOLVE_LOCK:
        if len(SOLVES) >= MAX_SOLVES:
            SOLVES.pop(next(iter(SOLVES)))
        SOLVES[solve_id] = {
            "trace": result.get("trace") or [],
            "fields": result.get("fields") or {},
            "evidence": result.get("evidence") or [],
            "answer": result.get("answer") or "",
            "category": result.get("category"),
        }
    return solve_id


def get_solve(solve_id: str) -> dict:
    with SOLVE_LOCK:
        record = SOLVES.get(solve_id)
    if record is None:
        raise ApiError("NOT_FOUND", "求解记录不存在或已过期", {"solve_id": solve_id})
    return record


def resolve_anchor(store, anchor: dict, settings=None) -> tuple[dict, str]:
    """定位作品：本地 Tier 1 快照优先，定位不到再走官方 API 兜底。

    返回 (作品, 来源)。本地命中是常态；API 兜底只在配置了 `APP_QINGCI_KEY` 时启用，
    且**必须唯一命中**才返回——定位不到就明确报错，不猜。

    这是三层兜底的「换数据源」部分；智能体编排（core/agent.py）在此之上再加
    「换算法」的一层。
    """
    try:
        return store.resolve(anchor["label"], anchor["poet"], anchor["tune"], anchor["first"]), "Tier 1"
    except ApiError as local_error:
        if not qingci.enabled():
            raise
        work = qingci.resolve_work(
            anchor["label"], anchor["poet"], anchor["tune"], anchor["first"]
        )
        if work is None:
            raise local_error
        work["_source"] = "官方 API 实时查询"
        return work, "官方 API 实时查询"


def solve_parts(category: str, anchors: list[dict], works: list[dict], sources: set[str]) -> dict:
    """给定类别、锚点与作品，产出确定性解。智能体编排也复用这一段。"""
    result = SOLVERS[category](anchors, works)
    result["status"] = "ok"
    # 来源标注：本地快照还是官方 API 实时查询，必须让使用者看得见
    result["source"] = "官方 API 实时查询" if "官方 API 实时查询" in sources else "Tier 1 官方快照"
    # 字段中文名与释义：机器字段名（甲_ratio、diff）用户看不懂，
    # 这里按外置规则逐个翻译，界面直接显示中文名 + 说明。
    result["field_meta"] = rules.field_meta(result.get("fields") or {})
    return result


def solve(question: str, store, settings=None) -> dict:
    if not question or not question.strip():
        raise ApiError("INVALID_INPUT", "题面为空")
    category = detect_category(question)
    anchors = parse_anchors(question)
    resolved = [resolve_anchor(store, anchor, settings) for anchor in anchors]
    works = [item[0] for item in resolved]
    sources = {item[1] for item in resolved}
    result = solve_parts(category, anchors, works, sources)
    result["solve_id"] = _register(result)
    return result

"""情感候选（板块 G）：事实卡 → 模型 → 四道闸门，外加一条「禁止推断」检查。

分两层（L1/L2 见分册 B §B4.1）：
- **L1 产品候选**：单句给方向候选 + 强度，永远标「未验证」，过不了闸门就不展示；
- **L2 基准集**：同一套提示词与解析逻辑，用于批量标注（见 scripts/label_sentiment.py）。

「禁止推断」是本模块自己加的第五道闸门：越界词表只拦价值判断（优于、更胜一筹…），
拦不住「此为悼亡之作」「作者中年失意」这类生平/事件推断，因此单独成闸。
"""

import json
import re
from collections import Counter

from . import sidecar

LABELS = [
    "思归怀人",
    "羁旅漂泊",
    "愁苦失意",
    "闲适自得",
    "清高自守",
    "伤春悲秋",
    "现实关怀",
    "言志用世",
    "无法判定",
]

STRENGTHS = (0, 1, 2, 3)

# 推断类禁用表述（ADR-0006 / 规范 §6）：出现即判该条作废
INFERENCE_PATTERNS = (
    "作者生平", "作者中年", "作者晚年", "仕途", "官职", "遭际", "身世",
    "悼亡", "悼念", "创作动机", "写作缘起", "寓意", "比肩", "胜过", "绝唱",
    "此为", "作于", "写于",
)

BOUNDARY_TEXT = (
    "仅据本句的形式线索，不能推出作者生平、具体事件、创作动机或作品优劣；"
    "方向与强度均为候选，未验证。"
)

# 三种提示变体：同一份规范，换措辞、换选项顺序，用于制造独立标注
VARIANTS = {
    "A": (
        "你是词学标注员。请按《情感候选标注规范 V1》给每一句标注情感方向候选。",
        "只依据句中可见的形式线索判断方向与强度；信息不足时选「无法判定」，不要猜。",
    ),
    "B": (
        "请逐句做词作情感方向标注，遵循《情感候选标注规范 V1》的闭集与边界。",
        "证据要来自句子本身；无法归入任一方向或只有纯写景时，一律给「无法判定」。",
    ),
    "C": (
        "依照《情感候选标注规范 V1》，为下列句子各选一个情感方向并给出强度。",
        "宁可判「无法判定」也不要给出没有句内依据的方向；禁止推断作者、事件与动机。",
    ),
    # 评估轮：独立于基准集构建，措辞与 A/B/C 都不同，避免"用构建者评估构建结果"
    "D": (
        "请为下列词句各标注一个情感方向与强度，依据是《情感候选标注规范 V1》。",
        "只依据句中文字本身；任何需要背景知识才能确定的判断，都应给出「无法判定」。",
    ),
    # 仲裁轮：规范更严（必须回到句内字面证据），并要写明争议理由
    "AR": (
        "你是仲裁员。同一批句子已有三轮独立标注且存在分歧，"
        "请依照《情感候选标注规范 V1》给出最终判定。",
        "必须回到句内可见的字面证据再判断；句内证据不足以支撑任何方向时，"
        "最终判定就是「无法判定」，不要在几个都不够的方向之间硬选。"
        "除 label/strength 外，还要在 reason 里写明本句的争议点与裁决依据。",
    ),
}


def violates_inference(text: str) -> list[str]:
    return [pattern for pattern in INFERENCE_PATTERNS if pattern in (text or "")]


def _extract_json(content: str) -> dict | None:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def build_messages(items: list[dict], variant: str = "A", labels: list[str] | None = None) -> tuple[str, str]:
    """构造标注请求，返回 (system, task)。items 需含 id 与 text。

    标注脚本（批量）与产品候选（单句）共用同一份措辞，避免"基准集怎么标"
    与"产品怎么标"出现两套口径而让评估失真。
    """
    head, tail = VARIANTS.get(variant, VARIANTS["A"])
    order = labels or LABELS
    lines = []
    for item in items:
        lines.append(
            '{"id": "%s", "text": "%s"}'
            % (item["id"], (item.get("text") or "").replace('"', "'"))
        )
    user = (
        head
        + "\n候选方向集合（闭集，只能从中选）："
        + "、".join(order)
        + "\n强度取值：0 无 / 1 弱 / 2 中 / 3 强\n"
        + "待标注句子（JSON 数组）：\n"
        + "\n".join(lines)
        + "\n\n"
        + tail
        + '\n严格只输出 JSON：{"items": [{"id": "...", "label": "...", "strength": 0,'
        + ' "reason": "≤30字句内线索", "boundary": "≤30字不能推出什么"}]}'
    )
    system = (
        "你是严谨的中文标注员，只依据给定事实卡写候选标注，只输出 JSON，不写任何多余文字。"
        "禁止推断作者生平、具体事件、创作动机与作品优劣；禁止评价文学价值高低。"
        "输出 items 数组，每项固定含 id、label、strength、reason、boundary 五个字段；"
        "verification 一律为「未验证」。"
    )
    return system, user


def extract_label_strength(row: dict) -> tuple[str, int | None]:
    """从一条标注里取 (方向, 强度)。

    兼容两种形态：字段在顶层，或被模型嵌进 draft 对象里——两种都出现过，
    但**输出契约只有一种**（见 build_messages 的 system）；这里只是不因形态差异丢数据。
    """
    source = row
    if isinstance(row.get("draft"), dict):
        source = {**row, **row["draft"]}
    label = str(source.get("label") or "")
    if label not in LABELS:
        return "", None
    try:
        strength = int(source.get("strength"))
    except (TypeError, ValueError):
        return label, None
    return label, (strength if strength in STRENGTHS else None)


def parse_items(content: str, expected_ids: list[str]) -> dict[str, dict]:
    """从模型输出里取每句的标注；解析不到的 id 直接缺席，不猜。"""
    parsed = _extract_json(content)
    if parsed is None:
        return {}
    out: dict[str, dict] = {}
    for row in parsed.get("items") or []:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "")
        label, strength = extract_label_strength(row)
        if item_id not in expected_ids or not label:
            continue
        reason = row.get("reason")
        if not reason and isinstance(row.get("draft"), dict):
            reason = row["draft"].get("reason")
        out[item_id] = {
            "label": label,
            "strength": strength,
            "reason": str(reason or row.get("basis") or "")[:60],
            "boundary": str(row.get("boundary") or "")[:60],
        }
    return out


def sentence_fact_card(item: dict) -> dict:
    """逐句事实卡：官方逐句计数 + 词牌/朝代 + 本地可见的意象线索。

    意象线索只列**句内**命中的词，不做题材归类（避免把词频当语义）。
    """
    # 自由文本没有官方逐句计数：ping/ze 可能是 None，这时只给字面线索，
    # 不伪造比例（事实卡里保持 None，界面据此说明"无官方计数"）
    ping = item.get("ping")
    ze = item.get("ze")
    total = (ping or 0) + (ze or 0)
    ratio = round(float(ze) / total * 100, 1) if total else None
    images = [word for word in IMAGE_HINTS if word in (item.get("text") or "")]
    return {
        "sentence": {"id": item["id"], "text": item["text"], "line_no": item.get("line_no")},
        "work": {
            "work_id": item.get("work_id"),
            "title": item.get("title"),
            "tune": item.get("tune"),
            "poet": item.get("poet"),
            "period": item.get("period"),
        },
        "facts": {
            "characters": item.get("chars"),
            "ping": ping,
            "ze": ze,
            "ze_ratio": ratio,
            "image_hints": images,
        },
        "has_official_counts": bool(total),
        "source": "Tier 1 官方逐句计数 + 句内字面线索",
        "tier_label": "官方标注（逐句计数）· 本地计算（意象线索）",
    }


IMAGE_HINTS = (
    "春", "秋", "月", "风", "雨", "雪", "霜", "露", "花", "柳", "燕", "雁",
    "梦", "酒", "灯", "楼", "舟", "马", "书", "剑", "泪", "愁", "魂", "影",
    "香", "声", "云", "山", "水", "红", "寒", "暖", "烟", "月",
)


def to_standard(parsed: dict | None) -> dict | None:
    """把模型输出折成闸门认的四字段；结构不合法返回 None。"""
    if not isinstance(parsed, dict):
        return None
    rows = parsed.get("items") or []
    if not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    label, strength = extract_label_strength(row)
    if not label or strength is None:
        return None
    reason = str(row.get("reason") or "")[:60]
    if not reason and isinstance(row.get("basis"), str):
        reason = row["basis"][:60]
    boundary = str(row.get("boundary") or "")[:60] or BOUNDARY_TEXT
    draft = "%s（强度%s）：%s" % (label, strength, reason or "句内线索不足")
    basis = ["label:" + label, "strength:%d" % strength]
    return {"draft": draft, "basis": basis, "verification": "未验证", "boundary": boundary}


def candidate(item: dict, model: str = sidecar.DEFAULT_MODEL, caller=None) -> dict:
    """L1 产品候选：单句情感方向候选，过不了闸门不展示。"""
    card = sentence_fact_card(item)
    system, task = build_messages([item], "A")
    extra = {"inference": True}
    result = sidecar.guarded(
        card, system, task, model, caller, extra_checks=extra, transform=to_standard
    )
    violations = result.get("violations") or {}
    raw = result.get("candidate") or {}
    hits = violates_inference(str(raw.get("draft") or "") + str(raw.get("reason") or ""))
    if hits:
        extra["inference"] = False
        result["checks"]["inference"] = False
        violations["inference"] = hits
        result["violations"] = violations
        result["status"] = "blocked"
        result["candidate"] = None
        result["fallback_reason"] = "候选含作者生平/事件类推断，已拦截"
    result["fact_card"] = card
    result["labels"] = LABELS
    candidate_obj = result.get("candidate") or {}
    basis = candidate_obj.get("basis") or []
    result["direction"] = next(
        (str(item).split(":", 1)[1] for item in basis if str(item).startswith("label:")), None
    )
    result["strength"] = next(
        (int(str(item).split(":", 1)[1]) for item in basis if str(item).startswith("strength:")), None
    )
    return result


SENTIMENT_SYSTEM = (
    "你是词学标注员，只依据给定事实卡写候选标注。只输出 JSON，字段固定："
    "draft、basis、verification（必须为「未验证」）、boundary。"
    "禁止推断作者生平、具体事件、创作动机与作品优劣；"
    "禁止评价文学价值高低；draft 与 basis 中不得出现事实卡以外的数字。"
)


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's κ：标注者间一致性。两个标注者都有效时才算。"""
    pairs = [(x, y) for x, y in zip(a, b) if x and y]
    if not pairs:
        return 0.0
    left = [p[0] for p in pairs]
    right = [p[1] for p in pairs]
    total = len(pairs)
    observed = sum(1 for x, y in pairs if x == y) / total
    count_a, count_b = Counter(left), Counter(right)
    expected = sum((count_a[c] / total) * (count_b[c] / total) for c in set(count_a) | set(count_b))
    if expected >= 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def majority(labels: list[str]) -> tuple[str, bool]:
    """多数票；没有过半的多数返回（多数标签, False）表示需仲裁。"""
    if not labels:
        return "", False
    counter = Counter(labels)
    label, count = counter.most_common(1)[0]
    return label, count * 2 > len(labels)

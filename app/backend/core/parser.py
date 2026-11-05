"""自由文本解析（板块 A）与体式候选（契约 §3.2 / V6.1 §13.1 案例①）。

**边界，必须如实对外**：
- 逐字四声与逐句平仄计数都是**本地字典计算**，不是官方标注；官方 prosody 只在库内作品上存在。
- 体式候选只比对 Tier 1 官方 423 篇里**已经出现过的句式字数序列**，不引入任何未授权词谱数据，
  也不宣称"词谱校勘"。句式不唯一时给多候选并要求人工确认，绝不静默取第一条。
- 分阕位置按 `max(1, floor(句数/2))` 给候选，是本地推测，不是官方分段。
- 这正是 V6.1 §1.7 说的泛化杠杆：评测集换作品/换写法时，先能解析、再给候选。
"""

import re
import threading
import uuid
from collections import defaultdict

from . import chars as char_core
from . import engine
from . import rhyme
from . import tier2
from .errors import ApiError

SENTENCE_END = re.compile(r"[。！？；\n]+")
MAX_PARSES = 50

PARSES: dict[str, dict] = {}
_LOCK = threading.Lock()

TIER_LABEL = "非官方口径 · 本地计算"
BOUNDARY = (
    "逐字四声与逐句计数为本地字典计算，非官方标注；"
    "体式候选只比对官方 423 篇里出现过的句式字数序列，不是词谱校勘；"
    "分阕位置为本地推测。数值可能偏差，不能作为官方口径使用。"
)


def split_lines(text: str) -> list[str]:
    """按句末标点分句；句内顿号、逗号保留，与官方 lines 的字数口径一致。"""
    return [part.strip() for part in SENTENCE_END.split(text or "") if part.strip()]


def _cjk(text: str) -> str:
    """定位用的归一化：与逐字层同一口径，扩展区汉字也算内容。"""
    return "".join(char_core.content_chars(text or ""))


def _pattern(lines: list[dict]) -> tuple[int, ...]:
    return tuple(line["character_count"] for line in lines)


def form_templates(store) -> dict[str, dict[tuple[int, ...], list[int]]]:
    """从官方快照反推每个词牌出现过的句式；构建一次即可，423 篇规模很小。"""
    templates: dict[str, dict[tuple[int, ...], list[int]]] = defaultdict(lambda: defaultdict(list))
    for work in store.all():
        tune = work.get("tune_pattern") or ""
        if not tune:
            continue
        _, lines = engine._prosody_lines(work)
        templates[tune][_pattern(lines)].append(int(work["id"]))
    return {tune: dict(forms) for tune, forms in templates.items()}


def tune_candidates(store, target: tuple[int, ...], tune_hint: str = "") -> dict:
    """给句式候选 + 置信度；同词牌多句式时明确标 ambiguous（案例①要用）。"""
    templates = form_templates(store)
    hint = (tune_hint or "").strip()
    out = []
    for tune, forms in templates.items():
        if hint and tune != hint:
            continue
        for index, (pattern, ids) in enumerate(
            sorted(forms.items(), key=lambda item: (-len(item[1]), item[0]))
        ):
            if pattern == target:
                score = 1.0
                reason = "句式字数序列与库内作品完全一致"
            elif len(pattern) == len(target):
                distance = sum(abs(a - b) for a, b in zip(pattern, target))
                if distance > 2:
                    continue
                score = round(max(0.35, 0.8 - distance * 0.15), 2)
                reason = "句数一致，累计字数差 %d 字，仅供参考" % distance
            else:
                continue
            out.append(
                {
                    "form_id": "%s#%d" % (tune, index + 1),
                    "tune": tune,
                    "pattern": list(pattern),
                    "score": score,
                    "confidence": (
                        "较高" if score == 1.0 and len(forms) == 1
                        else "需人工确认" if score == 1.0
                        else "较低"
                    ),
                    "reasons": [reason],
                    "sample_work_ids": ids[:3],
                    "sample_count": len(ids),
                    "tier_label": "Tier 1 官方快照 · 句式统计",
                }
            )
    out.sort(key=lambda item: (-item["score"], -item["sample_count"], item["tune"]))
    out = out[:8]

    hint_forms = templates.get(hint, {})
    ambiguous = bool(hint and len(hint_forms) > 1)
    return {
        "candidates": out,
        "ambiguous": ambiguous,
        "hint_form_count": len(hint_forms) if hint else None,
        "target_pattern": list(target),
        "boundary": (
            "词牌「%s」在官方 423 篇里有 %d 种句式，同调甚至同字数也不能唯一定体；"
            "以下为候选，需人工确认。" % (hint, len(hint_forms))
            if ambiguous
            else "候选只表示与库内已见句式的吻合度，不代表词谱身份判定；无匹配时保持无法归体。"
        ),
        "tier_label": TIER_LABEL,
    }


def locate(store, text: str, poet: str = "", tune: str = "") -> dict | None:
    """两层兜底（V6.1 §1.7）：

    1. 官方 423 篇冻结快照：逐字一致即指回官方口径页面；
    2. Tier 2a 同源带调拼音语料：第一层定位不到时在这里找，
       结果**一律非官方口径**（带警示），不能与官方计数混用。
    """
    target = _cjk(text)
    if not target:
        return None
    for work in store.all():
        if _cjk(work.get("text") or "") == target:
            return {
                "tier": "Tier 1",
                "tier_label": "Tier 1 官方快照",
                "work_id": int(work["id"]),
                "title": work.get("title"),
                "tune": work.get("tune_pattern"),
                "poet": (work.get("poet") or {}).get("name", ""),
                "official": True,
                "hint": "该文本与官方 423 篇中的作品逐字一致，官方平仄计数请走单篇解析。",
                "warning": None,
            }
    if tier2.available():
        first_line = text.split("。")[0].split("\n")[0]
        hits = tier2.locate(first_line, poet, tune)
        if hits:
            hit = hits[0]
            return {
                "tier": hit["tier"],
                "tier_label": hit["tier_label"],
                "work_id": hit["work_id"],
                "title": hit["title"],
                "tune": hit["tune"],
                "poet": hit["poet"],
                "official": False,
                "hint": "该文本不在官方 423 篇内，已在同源带调拼音语料中定位；以下声律为非官方口径。",
                "warning": hit["warning"],
                "tier2": hit,
            }
    return None


def parse(store, text: str, tune_hint: str = "") -> dict:
    segments = split_lines(text)
    if not segments:
        raise ApiError("INVALID_INPUT", "没有可解析的词文", {"hint": "请只粘贴词正文，句末用句号/问号/叹号"})
    if len(segments) > 40:
        raise ApiError("INVALID_INPUT", "句数超过 40，疑似不是一首词", {"lines": len(segments)})

    lines = []
    unknown = []
    located = locate(store, text)
    tier2_work = (located or {}).get("tier2")
    for index, segment in enumerate(segments, start=1):
        items = char_core.annotate(segment)
        if tier2_work:
            # Tier 2a 命中：逐字读音改用同源带调拼音（比今音首读更准）
            tier2_items = tier2.chars_for(tier2_work, index - 1)
            if tier2_items:
                items = tier2_items
        ping, ze = char_core.count(items)
        lines.append(
            {
                "line_no": index,
                "text": segment,
                "character_count": len(items),
                "ping_count": ping,
                "ze_count": ze,
                "chars": items,
                # 句末字与《词林正韵》韵部提示（不是与词谱韵位的校勘）
                "line_end": rhyme.line_end_info(segment),
            }
        )
        for item in items:
            if item["ping_ze"] == "未知":
                unknown.append(
                    {
                        "line_no": index,
                        "char": item["char"],
                        "reason": item["unknown_reason"] or "字典无该字声调记录",
                    }
                )

    pattern = _pattern(lines)
    result = {
        "lines": lines,
        "pattern": list(pattern),
        "split_after": max(1, len(lines) // 2),
        "unknown": unknown,
        "unknown_count": len(unknown),
        "located": located,
        "tier_label": TIER_LABEL if not tier2_work else tier2.TIER_LABEL,
        "boundary": (
            BOUNDARY
            if not tier2_work
            else BOUNDARY + " 本次结果来自 Tier 2a 同源带调拼音，属非官方口径。"
        ),
        "rhyme_boundary": rhyme.boundary(),
        "source_note": (
            "逐字四声与逐句平仄均由本地字典计算；官方只给库内作品的逐句计数；"
            "分阕位置为本地推测。"
            if not tier2_work
            else "逐字读音与逐句平仄来自 Tier 2a 同源带调拼音；"
            "官方只给库内作品的逐句计数；分阕位置为本地推测。"
        ),
        "tune_hint": tune_hint,
        "tunes": tune_candidates(store, pattern, tune_hint),
    }
    return result


def save(result: dict) -> str:
    parse_id = "parse_" + uuid.uuid4().hex[:12]
    result = dict(result)
    result["parse_id"] = parse_id
    with _LOCK:
        if len(PARSES) >= MAX_PARSES:
            PARSES.pop(next(iter(PARSES)))
        PARSES[parse_id] = result
    return parse_id


def get(parse_id: str) -> dict:
    with _LOCK:
        result = PARSES.get(parse_id)
    if result is None:
        raise ApiError("NOT_FOUND", "解析结果不存在或已过期", {"parse_id": parse_id})
    return result

"""论证辅助生成：事实卡 + 选中的证据 → 模型草稿（走统一四道闸门）。

为什么要单独做这一层：论证辅助页早期只把证据卡拼成模板，
页面自己都写着「草稿由模板拼装，未经模型生成」——榜题要的是
「论证辅助**生成**」，模板拼装不算。

纪律（与 `core/agent.py` 一致）：
- 数字**永远来自事实卡**，模型不产出判分字段、不换算、不新增；
- 只把用户**勾选的证据**交给模型，没勾就不生成（不给模型自由发挥的余地）；
- 结果一律标「未验证」，附边界声明；
- 复用 `sidecar.guarded()`，四道闸门只有一套判定逻辑；
- 模型不可用时 `guarded` 返回 degraded，前端回落到模板拼装并如实显示。
"""

import json

from . import sidecar
from .errors import ApiError

ARGUMENT_SYSTEM_PROMPT = (
    "你是词律研究助手，只能依据给定的事实卡与用户勾选的证据写一段论证草稿。"
    "禁止计算或改写任何平仄、比例、句数、字数；禁止评价文学价值高低；"
    "禁止推断作者生平、创作动机、时代因果或作品情感。"
    "不得引用事实卡以外的作品、词人或事件。"
    "只输出 JSON，字段固定：draft（论证草稿，120 字以内，可含「主张/证据/限定」三段）、"
    "basis（数组，逐条指向事实卡字段名）、"
    "verification（必须为「未验证」）、boundary（说明不能推出什么）。"
    "draft 中出现的每一个阿拉伯数字都必须直接来自事实卡，不得换算、不得新增（包括 0 与 100%）；"
    "中文数字（如「十二句」）同样不得与事实卡冲突。"
)


def _selected_card(card: dict, refs: list[str]) -> dict:
    """按前端证据卡的 `ref`（facts.xxx / quote.N）裁出子集，只把勾选项交给模型。"""
    facts = card.get("facts") or {}
    derived = card.get("derived") or {}
    quotes = card.get("quote_candidates") or []
    keep_facts, keep_derived, keep_quotes = {}, {}, []
    for ref in refs:
        if ref.startswith("facts."):
            key = ref[6:]
            if key in facts:
                keep_facts[key] = facts[key]
            elif key in derived:
                keep_derived[key] = derived[key]
        elif ref.startswith("quote."):
            try:
                line_no = int(ref.split(".", 1)[1])
            except ValueError:
                continue
            for quote in quotes:
                if quote.get("line_no") == line_no:
                    keep_quotes.append(quote)
                    break
    return {
        "work": card.get("work"),
        "source": card.get("source"),
        "tier_label": card.get("tier_label"),
        "facts": keep_facts,
        "derived": keep_derived,
        "quote_candidates": keep_quotes,
        "selected_refs": refs,
    }


def draft(store, work_id, claim: str, refs: list[str], model: str = sidecar.DEFAULT_MODEL,
          caller=None, settings=None) -> dict:
    """生成论证草稿。返回 guarded 的结果 + 裁剪后的事实卡。"""
    if not (claim or "").strip():
        raise ApiError("INVALID_INPUT", "需要给出主张")
    if not refs:
        raise ApiError("INVALID_INPUT", "至少勾选一条证据，模型不得凭空论证")
    if len(claim) > 200:
        raise ApiError("INVALID_INPUT", "主张过长（≤200 字）")

    card = sidecar.build_fact_card(store, work_id, settings)
    selected = _selected_card(card, refs)
    if not (selected["facts"] or selected["derived"] or selected["quote_candidates"]):
        raise ApiError("INVALID_INPUT", "勾选的证据在事实卡中找不到")

    task_prompt = (
        "事实卡中用户勾选的部分（唯一事实来源）：\n"
        + json.dumps(selected, ensure_ascii=False, indent=2)
        + "\n\n用户主张：" + claim.strip()
        + "\n\n任务：只用上面这些证据，把主张组织成一段有「主张 / 证据 / 限定」的论证草稿。"
    )
    result = sidecar.guarded(selected, ARGUMENT_SYSTEM_PROMPT, task_prompt, model, caller)
    result["fact_card"] = card
    result["selected_card"] = selected
    result["claim"] = claim.strip()
    return result

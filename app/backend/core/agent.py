"""智能体编排层：**引擎优先，答不上来才叫模型**。

这是三层兜底的第四层。前三层换的是「数据源」（本地快照 → 官方 API → 同源层），
这一层换的是「算法」——确定性规则覆盖不到的题型，由模型兜底作答。

三条纪律（写在这里，改代码前先读）：

1. **数字永远来自引擎**。模型只写解读与语义判断，**不产出判分字段**。
   判分口径（整段逐字 / 引文归一化 / C5 代理）由 `engine.solve` 独占，模型碰不到。
2. **模型兜底必须有事实卡**。没有事实卡就没有可核对的依据，**宁可报错不兜底**——
   模型在无依据时会编数字，比报错更糟。
3. **来源逐条标注**。输出里必须分得清：确定性求解 / 模型兜底（未验证）/ 模型解读（未验证）。
   评审一眼能看出哪些可信、哪些待核。

路由规则：

    题面
      ├─ 锚点解析 ── 失败 → 明确报错（模型也不知道怎么定位作品）
      ├─ 作品定位 ── 失败 → 明确报错（同上；模型会编数字）
      ├─ 引擎判类 + 求解
      │    ├─ 成功 → 确定性答案（可选再附一段模型解读）
      │    └─ 失败 → 模型兜底（带事实卡 + schema + 闸门）
      └─ 输出
"""

import json

from . import engine, sidecar
from .errors import ApiError

# 模型兜底用：题型不在规则覆盖内时，让模型依据事实卡自己作答
FALLBACK_SYSTEM_PROMPT = (
    "你是词律研究助手。给定的事实卡是唯一事实来源，题面是要回答的问题。"
    "事实卡里没有的数字一律不得出现——不要计算、不要推算、不要用常识补。"
    "禁止评价文学价值高低；禁止推断作者生平、创作动机或时代因果。"
    "只输出 JSON，字段固定：draft（答案正文）、basis（数组，逐条指向事实卡字段）、"
    "verification（必须为「未验证」）、boundary（说明不能推出什么）。"
    "draft 中出现的每一个阿拉伯数字都必须直接来自事实卡，不得换算、不得新增（包括 0 与 100%）。"
    "draft 直接作答，**不要提及「事实卡」「数据」「根据资料」这类来源表述**——"
    "事实卡是给你看的依据，不是写给读者的开场白。"
)

# 模型解读用：确定性答案之上再写一段解释
NARRATIVE_SYSTEM_PROMPT = (
    "你是词律研究助手。事实卡与确定性计算结果是唯一事实来源，"
    "请解释这些形式指标说明什么、以及不能说明什么。"
    "禁止计算或改写任何平仄、比例、句数、字数；禁止评价文学价值高低；"
    "禁止推断作者生平、创作动机或时代因果。"
    "只输出 JSON，字段固定：draft（不超过 120 字）、basis（数组，逐条指向事实卡字段名）、"
    "verification（必须为「未验证」）、boundary（说明不能推出什么）。"
    "draft 中出现的每一个阿拉伯数字都必须直接来自事实卡或计算结果，不得换算、不得新增"
    "（包括 0 与 100%）；中文数字（如「十二句」）同样不得与事实卡冲突。"
    "draft 直接写解读，**不要提及「事实卡」「计算结果」这类内部表述**。"
)

MODE_DETERMINISTIC = "确定性求解"
MODE_WITH_NARRATIVE = "确定性求解 + 模型解读"
MODE_FALLBACK = "模型兜底（未验证）"


def _narrate(store, anchors, works, question, model, caller=None) -> dict:
    """在确定性答案之上附一段模型解读（失败不影响主体答案）。"""
    try:
        cards = sidecar.build_fact_cards(
            [(anchor["label"], work) for anchor, work in zip(anchors, works)]
        )
    except ApiError:
        return {"status": "degraded", "candidate": None, "checks": None,
                "violations": {}, "model": model, "latency_ms": 0,
                "fallback_reason": "事实卡不可用"}
    task_prompt = (
        "事实卡（唯一事实来源）：\n"
        + json.dumps(cards, ensure_ascii=False, indent=2)
        + "\n\n任务：解释上面这些形式指标说明什么、不能说明什么。"
        + ("\n用户问题：" + question if question else "")
    )
    return sidecar.guarded(cards, NARRATIVE_SYSTEM_PROMPT, task_prompt, model, caller)


def _model_fallback(cards, question, model, caller=None) -> dict:
    """题型不在规则覆盖内：由模型依据事实卡作答（带闸门）。"""
    task_prompt = (
        "事实卡（唯一事实来源）：\n"
        + json.dumps(cards, ensure_ascii=False, indent=2)
        + "\n\n题面：\n"
        + (question or "")
        + "\n\n任务：依据事实卡回答题面。只能使用事实卡里的数字。"
    )
    return sidecar.guarded(cards, FALLBACK_SYSTEM_PROMPT, task_prompt, model, caller)


def answer(store, settings, question, narrative: bool = True, model: str | None = None,
           caller=None) -> dict:
    """智能体作答入口。

    返回结构（字段与 `/api/solve` 对齐，另加智能体特有字段）：
        status / mode / category / answer / fields / field_meta /
        trace / evidence / narrative / source / fallback / boundary
    """
    if not question or not question.strip():
        raise ApiError("INVALID_INPUT", "问题不能为空")
    model = model or sidecar.DEFAULT_MODEL

    # ── 1. 锚点解析：解析不出来 → 模型也无法定位作品，明确报错
    anchors = engine.parse_anchors(question)

    # ── 2. 作品定位（本地 → 官方 API）；定位不到 → 明确报错，不交给模型猜
    resolved = [engine.resolve_anchor(store, anchor, settings) for anchor in anchors]
    works = [item[0] for item in resolved]
    sources = {item[1] for item in resolved}
    data_source = "官方 API 实时查询" if "官方 API 实时查询" in sources else "Tier 1 官方快照"

    # ── 3. 引擎优先：判类 + 确定性求解
    category = ""
    rule_error: ApiError | None = None
    deterministic = None
    try:
        category = engine.detect_category(question)
    except ApiError as exc:
        rule_error = exc
    if category:
        try:
            deterministic = engine.solve_parts(category, anchors, works, sources)
        except ApiError as exc:
            rule_error = exc

    if deterministic is not None:
        result = dict(deterministic)
        result["mode"] = MODE_WITH_NARRATIVE if narrative else MODE_DETERMINISTIC
        result["source"] = data_source
        result["narrative"] = (
            _narrate(store, anchors, works, question, model, caller) if narrative else None
        )
        result["fallback"] = None
        result["boundary"] = (
            "数字与计算链由确定性引擎给出（可复算、可回跳原句）；"
            "模型解读是候选，一律标「未验证」，需要人自己核对。"
        )
        result["solve_id"] = engine._register(result)
        return result

    # ── 4. 模型兜底：仅在「事实卡拿得到」时允许
    try:
        cards = sidecar.build_fact_cards(
            [(anchor["label"], work) for anchor, work in zip(anchors, works)]
        )
    except ApiError as exc:
        raise ApiError(
            "INSUFFICIENT_DATA",
            "无法为这道题建立事实依据，不做模型兜底",
            {
                "reason": str(exc),
                "rule_error": rule_error.message if rule_error else "",
                "hint": "事实卡建不出来时模型会编造数字，因此这里明确报错而不是兜底",
            },
        ) from exc

    fallback = _model_fallback(cards, question, model, caller)
    candidate = fallback.get("candidate") or {}
    evidence = [
        {
            "work_id": item["work"]["id"],
            "title": item["work"]["title"],
            "poet": item["work"]["poet"],
            "line_ids": [quote["line_no"] for quote in item["quote_candidates"][:1]],
            "source": data_source,
            "tier_label": "官方标注（事实卡）",
            "quote": (item["quote_candidates"][0]["text"] if item["quote_candidates"] else ""),
            "quote_line_no": (item["quote_candidates"][0]["line_no"] if item["quote_candidates"] else 0),
        }
        for item in cards["works"]
    ]
    return {
        "status": fallback.get("status", "degraded"),
        "mode": MODE_FALLBACK,
        "category": "",
        "answer": candidate.get("draft", ""),
        "fields": {},
        "field_meta": {},
        "trace": [],
        "evidence": evidence,
        "narrative": fallback,
        "source": data_source,
        "fallback": {
            "reason": rule_error.message if rule_error else "题型不在确定性规则覆盖内",
            "rule_error": rule_error.code if rule_error else "",
            "note": "本次答案由模型依据事实卡生成，**未经确定性校验**；判分请以确定性字段为准。",
        },
        "boundary": (
            "规则未覆盖这类题型，已交给模型依据事实卡作答；"
            "模型答案一律标「未验证」，不作为判分依据。"
        ),
        "solve_id": None,
    }

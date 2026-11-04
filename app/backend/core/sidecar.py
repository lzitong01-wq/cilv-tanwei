"""受限模型副功能：事实卡 → 模型 → 四道闸门 → 候选解读。

铁律（ADR-0001）：模型不参与声律事实。它只消费事实卡、只产出候选，
且必须依次通过结构、事实、越界、标注四道闸门；任一不过即不展示草稿。
模型不可用时降级为「仅展示引擎事实」，不编造占位内容。
"""

import json
import os
import random
import re
import time
import urllib.error
import urllib.request

from . import corpus, engine, rules
from .errors import ApiError

DEFAULT_MODEL = rules.load()["default_model"]
SECOND_MODEL = rules.load()["second_model"]
MODEL_PROFILES = rules.model_profiles()
LABELING_PROFILES = rules.labeling_profiles()
BOUNDARY_CLAIMS = rules.boundary_claims()

NUMBER = re.compile(r"\d+(?:\.\d+)?")

SYSTEM_PROMPT = (
    "你是词律研究助手，只能依据给定的事实卡写候选解读。"
    "禁止计算或改写任何平仄、比例、句数、字数；禁止评价文学价值高低；"
    "禁止推断作者生平、创作动机或时代因果。"
    "只输出 JSON，字段固定：draft（不超过 80 字）、basis（数组，逐条指向事实卡字段名）、"
    "verification（必须为「未验证」）、boundary（说明不能推出什么）。"
    "draft 中出现的每一个阿拉伯数字都必须直接来自事实卡，不得换算、不得新增（包括 0 与 100%）；"
    "中文数字（如「十二句」）同样不得与事实卡冲突。"
)


def _model_url() -> str:
    return os.environ.get("APP_MODEL_URL", "http://10.27.66.12/v1/chat/completions")


def _model_key() -> str | None:
    return os.environ.get("SPARK_MODEL_KEY") or None


def build_fact_card(store, work_id, settings=None) -> dict:
    """只放引擎算得出、可复算的数字；不含任何模型生成内容。

    `work_id` 可以是官方快照的整数 id，也可以是同源层的 `t2a_` / `t2b_` 字符串 id
    （同源层的逐句平仄由带调拼音统计，口径标「非官方」）。
    """
    work = (
        corpus.load_work(settings, work_id)
        if settings is not None
        else store.get(work_id)
    )
    prosody, lines = engine._prosody_lines(work)
    pre, post = engine._split(lines, prosody["split_after_line"])
    ping = sum(line["ping_count"] for line in lines)
    ze = sum(line["ze_count"] for line in lines)
    pre_ratio = engine._ratio_of(pre)
    post_ratio = engine._ratio_of(post)
    change = engine.display_one(post_ratio - pre_ratio)
    max_count = max(line["character_count"] for line in lines)
    longest = [line["line_no"] for line in lines if line["character_count"] == max_count]
    post_sorted = sorted(post, key=lambda line: (-line["character_count"], line["line_no"]))

    card = {
        "work": {
            "id": work.get("id"),
            "title": work.get("title"),
            "tune": work.get("tune_pattern"),
            "poet": engine._poet_of(work),
            "period": work.get("period_label"),
        },
        "facts": {
            "lines": len(lines),
            "characters": sum(line["character_count"] for line in lines),
            "ping": ping,
            "ze": ze,
            "ze_ratio": engine.format_one(engine._ratio_of(lines)),
            "longest_line_nos": longest,
            "longest_chars": max_count,
            "split_after_line": prosody["split_after_line"],
            "pre_ratio": engine.format_one(pre_ratio),
            "post_ratio": engine.format_one(post_ratio),
            "change": engine.format_change(change),
        },
        "derived": {
            "total_tones": ping + ze,
            "post_minus_pre": engine.format_change(change),
        },
        "quote_candidates": [
            {"line_no": line["line_no"], "text": line["text"]} for line in post_sorted[:3]
        ],
        "source": work.get("_source") or "Tier 1 官方标注",
        "tier_label": work.get("_tier_label") or "官方标注",
    }
    return card


def build_fact_cards(items: list[tuple[str, dict]]) -> dict:
    """多篇作品的事实卡（对比题要 2–3 篇）。

    与 `build_fact_card`（单篇）同一套算法，额外补上**跨篇派生**：
    两篇的仄声比例差、前后半段变化差等——这些数字引擎算得出，
    模型写解读时就能引用，**事实闸门也才校验得住**。

    items: [(锚点标签, 作品对象), ...]
    """
    works_cards = []
    ratios: dict[str, float] = {}
    changes: dict[str, float] = {}
    for label, work in items:
        prosody, lines = engine._prosody_lines(work)
        pre, post = engine._split(lines, prosody["split_after_line"])
        ping = sum(line["ping_count"] for line in lines)
        ze = sum(line["ze_count"] for line in lines)
        pre_ratio = engine._ratio_of(pre)
        post_ratio = engine._ratio_of(post)
        change = engine.display_one(post_ratio - pre_ratio)
        max_count = max(line["character_count"] for line in lines)
        longest = [line["line_no"] for line in lines if line["character_count"] == max_count]
        post_sorted = sorted(post, key=lambda line: (-line["character_count"], line["line_no"]))
        ratios[label] = engine._ratio_of(lines)
        changes[label] = change
        works_cards.append(
            {
                "label": label,
                "work": {
                    "id": work.get("id"),
                    "title": work.get("title"),
                    "tune": work.get("tune_pattern"),
                    "poet": engine._poet_of(work),
                    "period": work.get("period_label"),
                },
                "facts": {
                    "lines": len(lines),
                    "characters": sum(line["character_count"] for line in lines),
                    "ping": ping,
                    "ze": ze,
                    "ze_ratio": engine.format_one(engine._ratio_of(lines)),
                    "longest_line_nos": longest,
                    "longest_chars": max_count,
                    "split_after_line": prosody["split_after_line"],
                    "pre_ratio": engine.format_one(pre_ratio),
                    "post_ratio": engine.format_one(post_ratio),
                    "change": engine.format_change(change),
                },
                "derived": {"total_tones": ping + ze},
                "quote_candidates": [
                    {"line_no": line["line_no"], "text": line["text"]} for line in post_sorted[:3]
                ],
            }
        )

    labels = list(ratios)
    derived: dict = {}
    if len(labels) == 2:
        left, right = labels
        diff = engine.display_one(abs(ratios[left] - ratios[right]))
        derived["%s_%s_仄声比例差" % (left, right)] = diff
        derived["较高者"] = left if ratios[left] >= ratios[right] else right
        derived["%s_前后段变化" % left] = engine.format_change(changes[left])
        derived["%s_前后段变化" % right] = engine.format_change(changes[right])

    # 来源逐篇取：官方快照标「官方标注」，同源层标「非官方口径」
    sources = {item.get("_source") or "Tier 1 官方标注" for item in items for item in [item[1]]}
    tier_labels = {item.get("_tier_label") or "官方标注" for item in items for item in [item[1]]}
    return {
        "works": works_cards,
        "facts": {
            "篇数": len(works_cards),
            "各篇仄声比例": {label: engine.format_one(ratios[label]) for label in labels},
        },
        "derived": derived,
        "source": "；".join(sorted(sources)),
        "tier_label": "；".join(sorted(tier_labels)),
    }


def _flatten_numbers(value, bucket: set[str]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        bucket.add(str(value))
        bucket.add("%.1f" % float(value))
    elif isinstance(value, str):
        for token in NUMBER.findall(value):
            bucket.add(token)
    elif isinstance(value, dict):
        for item in value.values():
            _flatten_numbers(item, bucket)
    elif isinstance(value, list):
        for item in value:
            _flatten_numbers(item, bucket)


def _parse_loose(text: str) -> dict | None:
    """宽容解析：先严格，再去代码围栏，再截取首尾花括号。"""
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    first, last = raw.find("{"), raw.rfind("}")
    if first != -1 and last > first:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _numbers_attributable(text: str, allowed: set[str]) -> list[str]:
    """文本中的每个数字都必须能归因到事实卡（含容差），否则返回违规项。"""
    allowed_values = []
    for token in allowed:
        try:
            allowed_values.append(float(token))
        except ValueError:
            continue
    violations = []
    for token in NUMBER.findall(text or ""):
        try:
            value = float(token)
        except ValueError:
            continue
        if not any(abs(value - known) <= 0.051 for known in allowed_values):
            violations.append(token)
    return violations


def _boundary_hits(text: str) -> list[str]:
    return [word for word in BOUNDARY_CLAIMS if word in (text or "")]


def _call_model(messages: list[dict], model: str, timeout: float, retries: int = 1, profile: dict | None = None) -> dict:
    # profile 可由调用方覆盖（如批量标注用更大的 token 预算），缺省仍走模型档案
    profile = profile or MODEL_PROFILES.get(model, MODEL_PROFILES[DEFAULT_MODEL])
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": profile["max_tokens"],
    }
    if profile["disable_thinking"]:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Content-Type": "application/json"}
    key = _model_key()
    if key:
        headers["Authorization"] = "Bearer " + key

    last_error = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            _model_url(), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            choice = (body.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            finish = choice.get("finish_reason")
            # 空正文多半是 token 预算被截断，属临时性失败，值得重试一次
            if not content.strip():
                last_error = "空正文（finish_reason=%s）" % finish
                if attempt < retries:
                    time.sleep(1.0 + random.uniform(0, 0.5))
                    continue
            return {
                "ok": True,
                "content": content,
                "finish_reason": finish,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "model": model,
            }
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = repr(exc)
        except (KeyError, json.JSONDecodeError) as exc:
            last_error = "响应不是合法 JSON：%r" % exc
        if attempt < retries:
            time.sleep(1.0 + random.uniform(0, 0.5))
    return {"ok": False, "content": "", "error": last_error, "latency_ms": 0, "model": model}


def guarded(fact_card: dict, system_prompt: str, task_prompt: str,
            model: str = DEFAULT_MODEL, caller=None,
            extra_checks: dict | None = None, transform=None) -> dict:
    """通用闸门：任何"事实卡 → 候选文本"的任务都走这里，保证只有一套判定逻辑。

    extra_checks：额外的布尔闸门（如情感模块的"禁止推断"），并入 checks 一起判定。
    transform：可选的字段映射，把模型自定义输出折成 draft/basis/verification/boundary；
               返回 None 表示结构不合法（按 structure 不通过处理）。
    """
    if model not in MODEL_PROFILES:
        raise ApiError("INVALID_INPUT", "不支持的模型", {"supported": list(MODEL_PROFILES)})
    caller = caller or _call_model
    profile = MODEL_PROFILES[model]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt},
    ]
    call = caller(messages, model, profile["timeout"])

    checks = {"structure": False, "facts": False, "boundary": False, "label": False}
    violations: dict = {}

    if not call.get("ok"):
        return {
            "status": "degraded",
            "candidate": None,
            "checks": checks,
            "violations": {},
            "model": model,
            "latency_ms": 0,
            "fallback_reason": call.get("error") or "模型不可用",
        }

    parsed = _parse_loose(call.get("content", ""))
    if parsed is not None and transform is not None:
        # 字段映射：把模型自定义输出折成标准四字段；返回 None 即结构不合法
        parsed = transform(parsed)
    checks["structure"] = parsed is not None
    if parsed is None:
        violations["structure"] = "模型输出无法解析为 JSON 对象"

    allowed: set[str] = set()
    _flatten_numbers(fact_card, allowed)
    # 0 属于低风险数字（"无未知字"这类陈述），放行以免误杀
    allowed.add("0")

    if parsed is not None:
        draft = str(parsed.get("draft") or "")
        basis = " ".join(str(item) for item in (parsed.get("basis") or []))
        # 只校验展示文本 draft；basis 是字段路径（如 authors[0]），其中的下标不是事实数字
        fact_violations = _numbers_attributable(draft, allowed)
        checks["facts"] = not fact_violations
        if fact_violations:
            violations["facts"] = fact_violations

        hits = _boundary_hits(draft + " " + basis)  # 越界仍看两处：依据也可能夹带价值判断
        checks["boundary"] = not hits
        if hits:
            violations["boundary"] = hits

        verification = str(parsed.get("verification") or "")
        boundary_field = str(parsed.get("boundary") or "")
        checks["label"] = ("未验证" in verification) and bool(boundary_field)
        if not checks["label"]:
            violations["label"] = "缺少「未验证」标注或边界说明"

    for name, ok in (extra_checks or {}).items():
        checks[name] = bool(ok)
    passed = all(checks.values())
    return {
        "status": "ok" if passed else "blocked",
        "candidate": {
            "draft": str(parsed.get("draft") or "") if parsed else "",
            "basis": parsed.get("basis") if parsed else [],
            "verification": parsed.get("verification") if parsed else None,
            "boundary": parsed.get("boundary") if parsed else None,
        },
        "checks": checks,
        "violations": violations,
        "model": model,
        "latency_ms": call.get("latency_ms", 0),
        "fallback_reason": None if passed else "未通过闸门，草稿不展示",
    }


def generate(store, work_id, question: str = "", model: str = DEFAULT_MODEL, caller=None,
             settings=None) -> dict:
    """事实卡 → 候选解读。

    `work_id` 可以是官方快照的整数 id，也可以是同源层的 `t2a_` / `t2b_` 字符串 id。
    """
    card = build_fact_card(store, work_id, settings)
    task_prompt = (
        "事实卡（唯一事实来源）：\n"
        + json.dumps(card, ensure_ascii=False, indent=2)
        + "\n\n任务：就这首词的形式特征写一段候选解读。"
        + ("用户问题：" + question if question else "")
    )
    result = guarded(card, SYSTEM_PROMPT, task_prompt, model, caller)
    result["fact_card"] = card
    return result

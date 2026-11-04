"""知识图谱：词人—作品—词牌—朝代四类节点。

边界（必须如实）：官方史证接口（history/events、poet-life、work-links）当前 total=0，
所以图谱只有元数据层关系，史证关系位保留但不虚构填充。

规模控制：全库 423 作品 + 243 词人 + 100 词牌不适合一次渲染，
默认只出「朝代 → 词牌 → 词人」的概览；聚焦时只取该节点的一跳邻居（上限 30）。
"""

import collections

from .errors import ApiError

MAX_NEIGHBORS = 30
OVERVIEW_TUNES = 12
OVERVIEW_POETS = 12

# 史证层没有数据就明说，任何视图都带这一句，避免客户端换焦点后丢掉免责说明
HISTORY_LAYER = "史证接口当前 total=0，关系位保留不虚构"


def _poet_of(work: dict) -> str:
    poet = work.get("poet")
    if isinstance(poet, dict):
        return poet.get("name") or ""
    return work.get("poet_name") or ""


def _node(node_id: str, node_type: str, label: str, weight: int = 1) -> dict:
    return {"id": node_id, "type": node_type, "label": label, "weight": weight}


def _edge(source: str, target: str, relation: str) -> dict:
    return {"source": source, "target": target, "relation": relation}


def overview(store) -> dict:
    works = store.all()
    by_period = collections.Counter()
    by_tune = collections.Counter()
    by_poet = collections.Counter()
    period_tune = collections.Counter()
    tune_poet = collections.Counter()

    for work in works:
        period = work.get("period_label") or "未标注"
        tune = work.get("tune_pattern") or ""
        poet = _poet_of(work)
        by_period[period] += 1
        if tune:
            by_tune[tune] += 1
            period_tune[(period, tune)] += 1
        if poet:
            by_poet[poet] += 1
            if tune:
                tune_poet[(tune, poet)] += 1

    nodes = []
    edges = []
    for period, count in by_period.most_common():
        nodes.append(_node("period:" + period, "period", period, count))
    for tune, count in by_tune.most_common(OVERVIEW_TUNES):
        nodes.append(_node("tune:" + tune, "tune", tune, count))
        for period in by_period:
            if period_tune.get((period, tune)):
                edges.append(_edge("period:" + period, "tune:" + tune, "收录"))
    for poet, count in by_poet.most_common(OVERVIEW_POETS):
        nodes.append(_node("poet:" + poet, "poet", poet, count))
        for tune in by_tune:
            if tune_poet.get((tune, poet)):
                edges.append(_edge("tune:" + tune, "poet:" + poet, "使用"))
                break

    return {
        "nodes": nodes,
        "edges": edges,
        "focus": None,
        "meta": {
            "works": len(works),
            "poets": len(by_poet),
            "tunes": len(by_tune),
            "periods": dict(by_period),
            "visible_nodes": len(nodes),
            "history_layer": HISTORY_LAYER,
        },
    }


def _focus_poet(store, poet: str) -> dict:
    works = [work for work in store.all() if _poet_of(work) == poet]
    if not works:
        raise ApiError("NOT_FOUND", "词人不在语料中", {"poet": poet})
    nodes = [_node("poet:" + poet, "poet", poet, len(works))]
    edges = []
    tunes = collections.Counter()
    periods = collections.Counter()
    for work in works:
        if work.get("tune_pattern"):
            tunes[work["tune_pattern"]] += 1
        periods[work.get("period_label") or "未标注"] += 1
        if len(nodes) <= MAX_NEIGHBORS:
            nodes.append(_node("work:%s" % work["id"], "work", work.get("title") or str(work["id"]), 1))
            edges.append(_edge("poet:" + poet, "work:%s" % work["id"], "创作"))
    for tune, count in tunes.most_common(8):
        nodes.append(_node("tune:" + tune, "tune", tune, count))
        edges.append(_edge("poet:" + poet, "tune:" + tune, "使用"))
    for period, count in periods.most_common(2):
        nodes.append(_node("period:" + period, "period", period, count))
        edges.append(_edge("poet:" + poet, "period:" + period, "归属"))
    return {
        "nodes": nodes[: MAX_NEIGHBORS + 12],
        "edges": edges,
        "focus": {"type": "poet", "id": "poet:" + poet, "label": poet},
        "meta": {"works": len(works), "tunes": len(tunes), "periods": dict(periods)},
    }


def _focus_tune(store, tune: str) -> dict:
    works = [work for work in store.all() if (work.get("tune_pattern") or "") == tune]
    if not works:
        raise ApiError("NOT_FOUND", "词牌不在语料中", {"tune": tune})
    nodes = [_node("tune:" + tune, "tune", tune, len(works))]
    edges = []
    poets = collections.Counter()
    periods = collections.Counter()
    for work in works:
        poet = _poet_of(work)
        if poet:
            poets[poet] += 1
        periods[work.get("period_label") or "未标注"] += 1
        if len(nodes) <= MAX_NEIGHBORS:
            nodes.append(_node("work:%s" % work["id"], "work", work.get("title") or str(work["id"]), 1))
            edges.append(_edge("tune:" + tune, "work:%s" % work["id"], "属于"))
    for poet, count in poets.most_common(10):
        nodes.append(_node("poet:" + poet, "poet", poet, count))
        edges.append(_edge("tune:" + tune, "poet:" + poet, "被使用"))
    for period, count in periods.most_common(2):
        nodes.append(_node("period:" + period, "period", period, count))
        edges.append(_edge("tune:" + tune, "period:" + period, "分布"))
    return {
        "nodes": nodes[: MAX_NEIGHBORS + 12],
        "edges": edges,
        "focus": {"type": "tune", "id": "tune:" + tune, "label": tune},
        "meta": {"works": len(works), "poets": len(poets), "periods": dict(periods)},
    }


def _focus_period(store, period: str) -> dict:
    works = [work for work in store.all() if (work.get("period_label") or "") == period]
    if not works:
        raise ApiError("NOT_FOUND", "朝代不在语料中", {"period": period})
    nodes = [_node("period:" + period, "period", period, len(works))]
    edges = []
    poets = collections.Counter()
    tunes = collections.Counter()
    for work in works:
        poet = _poet_of(work)
        if poet:
            poets[poet] += 1
        if work.get("tune_pattern"):
            tunes[work["tune_pattern"]] += 1
    for poet, count in poets.most_common(OVERVIEW_POETS):
        nodes.append(_node("poet:" + poet, "poet", poet, count))
        edges.append(_edge("period:" + period, "poet:" + poet, "收录"))
    for tune, count in tunes.most_common(OVERVIEW_TUNES):
        nodes.append(_node("tune:" + tune, "tune", tune, count))
        edges.append(_edge("period:" + period, "tune:" + tune, "收录"))
    return {
        "nodes": nodes,
        "edges": edges,
        "focus": {"type": "period", "id": "period:" + period, "label": period},
        "meta": {"works": len(works), "poets": len(poets), "tunes": len(tunes)},
    }


def build(store, focus: str = "", focus_type: str = "") -> dict:
    if not focus:
        result = overview(store)
    else:
        focus = focus.strip()
        kind = (focus_type or "").strip() or ("poet" if ":" not in focus else focus.split(":", 1)[0])
        value = focus.split(":", 1)[1] if ":" in focus else focus
        if kind == "poet":
            result = _focus_poet(store, value)
        elif kind == "tune":
            result = _focus_tune(store, value)
        elif kind == "period":
            result = _focus_period(store, value)
        else:
            raise ApiError("INVALID_INPUT", "focus_type 取 poet / tune / period", {"focus_type": kind})
    result["meta"].setdefault("history_layer", HISTORY_LAYER)
    return result

"""词人风格：形式特征查询、对照，以及基于同一事实卡的双模型解读。

分工写死在这里：
- 特征（作品数、词牌多样性、句长、仄比、长句率、题材词频）由构建期统计，**可复算**；
- 风格描述与对比结论**只能**由模型基于特征卡生成，且必须过四道闸门、标未验证。
"""

import json
import sqlite3
from pathlib import Path

from . import sidecar
from .errors import ApiError

STYLE_FIELDS = [
    "works",
    "tune_diversity",
    "mean_line_length",
    "line_length_sd",
    "ze_ratio",
    "long_line_rate",
]

FIELD_LABEL = {
    "works": "作品数",
    "tune_diversity": "词牌多样性",
    "mean_line_length": "平均句长",
    "line_length_sd": "句长标准差",
    "ze_ratio": "仄声比例",
    "long_line_rate": "长句句占比",
}

STYLE_SYSTEM = (
    "你是词学研究者，只能依据给定的事实卡做形式层面的比较。"
    "禁止计算或改写任何数字；禁止评价文学价值高低、作家优劣；"
    "禁止推断作者生平、创作动机或时代因果；禁止把形式特征等同于文学风格。"
    "只输出 JSON：draft（不超过 120 字）、basis（数组，指向事实卡字段名）、"
    "verification（必须为「未验证」）、boundary（说明不能推出什么）。"
)


def _connect(settings) -> sqlite3.Connection:
    path = Path(settings.style_index_path)
    if not path.is_file():
        raise ApiError(
            "NOT_FOUND",
            "风格索引未生成",
            {"expected_path": str(path), "hint": "运行 python app/scripts/build_style_index.py"},
        )
    connection = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_style(row: sqlite3.Row) -> dict:
    return {
        "poet": row["poet"],
        "works": row["works"],
        "tune_diversity": row["tune_diversity"],
        "mean_line_length": row["mean_line_length"],
        "line_length_sd": row["line_length_sd"],
        "ze_ratio": row["ze_ratio"],
        "long_line_rate": row["long_line_rate"],
        "top_tunes": json.loads(row["top_tunes"] or "[]"),
        "top_images": json.loads(row["top_images"] or "[]"),
        "basis": "Tier 2a 公开整理本（poetry-source 清词）",
        "tier_label": "公开语料·本地计算",
        "sample_note": "入库门槛：作者作品数 ≥ 20",
    }


def list_authors(settings, query: str = "", limit: int = 50) -> dict:
    connection = _connect(settings)
    try:
        if query:
            cursor = connection.execute(
                "SELECT poet, works FROM styles WHERE poet LIKE ? ORDER BY works DESC LIMIT ?",
                ("%" + query + "%", limit),
            )
        else:
            cursor = connection.execute(
                "SELECT poet, works FROM styles ORDER BY works DESC LIMIT ?", (limit,)
            )
        rows = [{"poet": row["poet"], "works": row["works"]} for row in cursor]
        total = connection.execute("SELECT count(*) FROM styles").fetchone()[0]
    finally:
        connection.close()
    return {"items": rows, "total": total, "query": query}


def get_style(settings, poet: str) -> dict:
    connection = _connect(settings)
    try:
        row = connection.execute("SELECT * FROM styles WHERE poet = ?", (poet,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ApiError(
            "NOT_FOUND",
            "该词人不在风格索引中",
            {"poet": poet, "hint": "仅收录作品数 ≥ 20 的作者"},
        )
    return _row_to_style(row)


def compare(settings, left: str, right: str) -> dict:
    if left == right:
        raise ApiError("INVALID_INPUT", "请选择两位不同词人")
    a = get_style(settings, left)
    b = get_style(settings, right)
    differences = []
    for field in STYLE_FIELDS:
        value_a, value_b = a.get(field), b.get(field)
        if value_a is None or value_b is None:
            differences.append({"field": field, "label": FIELD_LABEL[field], "a": value_a, "b": value_b, "delta": None})
            continue
        differences.append(
            {
                "field": field,
                "label": FIELD_LABEL[field],
                "a": value_a,
                "b": value_b,
                "delta": round(float(value_b) - float(value_a), 2),
            }
        )
    return {"a": a, "b": b, "differences": differences}


def _style_fact_card(pair: dict) -> dict:
    def slim(style: dict) -> dict:
        return {
            "poet": style["poet"],
            "works": style["works"],
            "tune_diversity": style["tune_diversity"],
            "mean_line_length": style["mean_line_length"],
            "line_length_sd": style["line_length_sd"],
            "ze_ratio": style["ze_ratio"],
            "long_line_rate": style["long_line_rate"],
            "top_tunes": style["top_tunes"],
            "top_images": style["top_images"],
        }

    return {
        "authors": [slim(pair["a"]), slim(pair["b"])],
        "differences": pair["differences"],
        "source": "Tier 2a 公开整理本（poetry-source 清词）",
        "tier_label": "公开语料·本地计算",
    }


def narrative(settings, left: str, right: str, model: str = sidecar.DEFAULT_MODEL, caller=None) -> dict:
    pair = compare(settings, left, right)
    card = _style_fact_card(pair)
    task_prompt = (
        "事实卡（两位词人的形式特征，唯一事实来源）：\n"
        + json.dumps(card, ensure_ascii=False, indent=2)
        + "\n\n任务：比较两人的形式特征差异，写一段候选解读。只描述句式、词牌、韵部与题材词频这类形式层面的事实。"
    )
    result = sidecar.guarded(card, STYLE_SYSTEM, task_prompt, model, caller)
    result["fact_card"] = card
    return result


def narrative_dual(settings, left: str, right: str, caller=None) -> dict:
    """两个模型各写一段，便于对照；任一失败不影响另一个。"""
    pair = compare(settings, left, right)
    card = _style_fact_card(pair)
    task_prompt = (
        "事实卡（两位词人的形式特征，唯一事实来源）：\n"
        + json.dumps(card, ensure_ascii=False, indent=2)
        + "\n\n任务：比较两人的形式特征差异，写一段候选解读。只描述句式、词牌、韵部与题材词频这类形式层面的事实。"
    )
    results = {}
    for label, model in (("qwen", sidecar.DEFAULT_MODEL), ("flash", sidecar.SECOND_MODEL)):
        results[label] = sidecar.guarded(card, STYLE_SYSTEM, task_prompt, model, caller)
    return {"fact_card": card, "models": results}

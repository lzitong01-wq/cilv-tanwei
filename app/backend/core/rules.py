"""规则外置层：从 rules.json 读判类措辞、公式口径与闸门词表。

V6.1 §1.7 与答辩 Q5 的工程保证：评测集与训练集同构但换作品、措辞可能漂移，
因此判类关键词与闸名词表必须能不发改擎代码就热修。加载失败或缺项**明确报错**，
不静默回退到内置默认值——否则会以为外置生效、实际仍跑旧规则。
"""

import json
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "rules.json"

REQUIRED = (
    "version",
    "contract",
    "categories",
    "formulas",
    "c5",
    "boundary_claims",
    "verification",
    "prompt_fields",
    "model_profiles",
    "labeling_profiles",
    "default_model",
    "second_model",
)


@lru_cache(maxsize=1)
def load() -> dict:
    if not RULES_PATH.is_file():
        raise RuntimeError("规则文件不存在：%s" % RULES_PATH)
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED if key not in data]
    if missing:
        raise RuntimeError("规则文件缺字段：%s（%s）" % ("、".join(missing), RULES_PATH))
    for category in data["categories"]:
        if not category.get("name"):
            raise RuntimeError("类别缺少 name：%r" % category)
        if not category.get("match") and not category.get("detect"):
            raise RuntimeError("类别缺少 match 或 detect：%r" % category)
    return data


def categories() -> list[dict]:
    return load()["categories"]


def category_names() -> list[str]:
    return [item["name"] for item in categories()]


def detect(question: str) -> str:
    """按配置顺序匹配题面措辞；匹配不到时由调用方抛 INVALID_INPUT。

    每类可写 `{"all": [...]}`（全部命中才算）或 `{"any": [...]}`（任一命中），
    也兼容旧的 `detect` 字符串列表（等价于 any）。
    多关键词比单关键词更抗措辞漂移——评测集与训练集同构但措辞可能变，
    这正是「规则外置可热修」的用处（V6.1 §1.7）。
    """
    for category in categories():
        match = category.get("match")
        if match is None:
            keywords = category.get("detect") or []
            if any(keyword in question for keyword in keywords):
                return category["name"]
            continue
        if match.get("all") and not all(keyword in question for keyword in match["all"]):
            continue
        anys = match.get("any") or []
        if anys and not any(keyword in question for keyword in anys):
            continue
        if match.get("all") or anys:
            return category["name"]
    return ""


def c5_text() -> tuple[str, str]:
    c5 = load()["c5"]
    return c5["boundary"], c5["status"]


def match_keywords(category: dict) -> tuple[list[str], str]:
    """把一类的匹配配置摊平成 (关键词列表, 匹配模式)。

    支持三种写法（V6.1 §1.7 热修时可能手写任一种）：
      - `match: {"all": [...]}`  → 全部命中才算，模式 "all"
      - `match: {"any": [...]}`  → 任一命中即可，模式 "any"
      - `detect: [...]`（旧格式）→ 等价于 any

    摊平是给界面用的：`/api/rules` 要能直接显示「这类看哪几个词、怎么算命中」，
    不该让前端自己猜 `match` 的结构。
    """
    match = category.get("match")
    if isinstance(match, dict):
        if match.get("all"):
            return list(match["all"]), "all"
        if match.get("any"):
            return list(match["any"]), "any"
        return [], "any"
    keywords = category.get("detect") or []
    return list(keywords), "any"


def view() -> dict:
    """对外视图：在原始规则上补一层界面可直接消费的字段。

    只**增**字段不删字段——`/api/rules` 仍是「当前生效规则」的完整快照，
    任何一项缺失都能在这里被看到。
    """
    data = load()
    categories = []
    for category in data["categories"]:
        keywords, mode = match_keywords(category)
        item = dict(category)
        item["detect"] = keywords
        item["match_mode"] = mode
        categories.append(item)
    result = dict(data)
    result["categories"] = categories
    return result


# 字段名里的作品锚点前缀（甲／乙／丙…），用于查 `{a}_xxx` 模板
_ANCHOR_CHARS = "甲乙丙丁戊己庚辛壬癸"


def field_meta(fields: dict) -> dict:
    """给求解结果的每个字段配「中文名 + 释义」。

    字段名是判分口径的**机器标识**（`甲_ratio`、`diff`），评审与用户看不懂。
    这里按外置的 `field_labels` 逐个翻译，未登记的字段原样保留并标注，
    **不隐藏**——少一个字段比多一句说明更糟。
    """
    catalog = load().get("field_labels") or {}
    result: dict = {}
    for key in fields:
        entry = catalog.get(key)
        if entry is None:
            for index, char in enumerate(key):
                if char in _ANCHOR_CHARS and key[index + 1 : index + 2] == "_":
                    entry = catalog.get("{%s}%s" % ("a", key[index + 1 :]))
                    if entry:
                        entry = dict(entry)
                        entry["label"] = "%s·%s" % (char, entry["label"])
                    break
        if entry is None:
            result[key] = {"label": key, "desc": "未登记的字段（外置规则里没有对应说明）"}
        else:
            result[key] = {"label": entry["label"], "desc": entry.get("desc", "")}
    return result


def boundary_claims() -> list[str]:
    return list(load()["boundary_claims"])


def model_profiles() -> dict:
    return dict(load()["model_profiles"])


def labeling_profiles() -> dict:
    """标注专用档位：批量标注一次要出十几条 JSON，token 预算比副功能大。"""
    return dict(load()["labeling_profiles"])

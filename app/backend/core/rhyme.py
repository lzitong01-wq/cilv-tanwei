"""句末字与韵部提示（**韵脚**）。

给逐句声律结果补上「这句最后一个汉字是什么、属《词林正韵》哪一部」。

诚实地划清边界：
- 词谱 `Ci_Tunes.json` 的 `sketch` 只给韵数与平仄，**不含韵部名**，
  所以「句末字属韵部 vs 谱标韵」的对齐做不了，这里**不判某句是否押韵**。
- 韵部名来自构建期派生的 `data/rhyme_index.json`（《词林正韵》字表），
  只作提示；查不到的字一律返回 `None` 并显式说明，不猜。
"""

import json
from functools import lru_cache
from pathlib import Path

_INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "rhyme_index.json"

def _is_hanzi(char: str) -> bool:
    """覆盖基本区、扩展 A 与扩展 B–G。

    扩展区汉字多半不在《词林正韵》字表里，但**仍要识别为汉字**——
    否则会被误报成「本句没有可识别的汉字」，而不是「字表未收录、韵部未知」。
    """
    if not char:
        return False
    code = ord(char)
    return 0x3400 <= code <= 0x9FFF or 0x20000 <= code <= 0x3FFFF


@lru_cache(maxsize=1)
def _index() -> dict:
    try:
        payload = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        return payload.get("items") or {}
    except (OSError, ValueError):
        return {}


def boundary() -> str:
    """给界面用的免责说明：缺数据时也要如实说。"""
    try:
        payload = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        return payload.get("boundary") or ""
    except (OSError, ValueError):
        return "韵部字表未就位，本次不提供韵脚提示。"


def line_end_info(text: str) -> dict:
    """取一句的句末汉字与韵部。

    返回 `{line_end_char, rhyme, rhyme_found, source, note}`。
    句末汉字取**最后一个汉字**（跳过标点与空白）；查不到韵部时
    `rhyme` 为 `None` 且 `rhyme_found=False`，绝不猜一个填上去。
    """
    chars = [ch for ch in (text or "") if _is_hanzi(ch)]
    if not chars:
        return {
            "line_end_char": None,
            "rhyme": None,
            "rhyme_found": False,
            "source": "派生事实（非官方标注）",
            "note": "本句没有可识别的汉字",
        }
    last = chars[-1]
    entry = _index().get(last)
    rhyme = entry.get("rhyme") if isinstance(entry, dict) else None
    return {
        "line_end_char": last,
        "rhyme": rhyme,
        "rhyme_found": bool(rhyme),
        "source": "《词林正韵》字表派生（非官方标注）",
        "note": (
            "韵部为字表提示，不是与词谱韵位的校勘结论"
            if rhyme
            else "字表未收录该字，韵部未知（不猜测）"
        ),
    }


def annotate_lines(lines: list) -> list:
    """给逐句结果批量补韵脚字段（只增不删，调用方原有字段全部保留）。"""
    out = []
    for line in lines or []:
        item = dict(line)
        item["line_end"] = line_end_info(item.get("text") or "")
        out.append(item)
    return out

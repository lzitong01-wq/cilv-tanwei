"""Tier 2a 定位与声律（V6.1 §1.7 三层兜底的第三层）。

第一层是官方 423 篇冻结快照，第二层是官方 live API（运行时零外网，按需启用），
第三层就是这里：**在同源带调拼音语料里定位作品，并用该拼音算声律**。

价值：评测集可能用训练集未出现的新作品。第一层定位不到时，这一层仍能给出
逐句平仄，而且用的是**同源带调拼音**（同源口径一致率 94.15%），比今音首读（91.57%）更准。

口径铁律：这一层的结果**一律是「非官方口径」**，数值可能偏差，
不能当官方标注使用；界面上必须带警示，不能与 Tier 1 混为一谈。
"""

import json
import sqlite3
import threading
from pathlib import Path

from . import chars as char_core
from . import sqlite_compat
from .errors import ApiError

_LOCK = threading.Lock()
_CACHE: dict = {}

TONE_MARKS = {
    "āēīōūǖ": 1, "áéíóúǘ": 2, "ǎěǐǒǔǚ": 3, "àèìòùǜ": 4,
}

TIER_LABEL = "Tier 2a 同源带调拼音 · 非官方口径"
WARNING = (
    "该作品不在官方 423 篇冻结快照内，以下声律由同源带调拼音计算，"
    "属非官方口径，数值可能偏差；官方计数为准时请以 Tier 1 为准。"
)


def _index_path() -> Path:
    # core/tier2.py → backend → app/data/
    return Path(__file__).resolve().parents[2] / "data" / "tier2_locator.sqlite3"


def available() -> bool:
    return _index_path().is_file()


def _connect() -> sqlite3.Connection:
    path = _index_path()
    if not path.is_file():
        raise ApiError(
            "NOT_FOUND",
            "Tier 2 定位索引未生成",
            {"expected_path": str(path), "hint": "运行 python app/scripts/build_tier2_locator.py"},
        )
    # 走兼容层：索引里有 FTS5(trigram) 表，系统 SQLite 过旧时打开就会失败。
    connection = sqlite_compat.connect("file:%s?mode=ro" % path.as_posix(), read_only=True)
    connection.row_factory = sqlite_compat.Row
    return connection


def tones_of(pinyin_text: str) -> list[int]:
    """按顺序取每个音节的调号（标点与音节粘连，只能靠调号计数）。"""
    tones = []
    for char in pinyin_text or "":
        for marks, tone in TONE_MARKS.items():
            if char in marks:
                tones.append(tone)
                break
    return tones


def _chars_from_pinyin(text: str, pinyin_text: str) -> list[dict]:
    """用同源拼音覆盖逐字读音：对不齐就返回空列表，不用猜。"""
    tones = tones_of(pinyin_text)
    items = char_core.annotate(text)
    if not items or len(tones) != len(items):
        return []
    out = []
    for item, tone in zip(items, tones):
        updated = dict(item)
        updated["tone"] = tone
        updated["tone_name"] = char_core.TONE_NAMES.get(tone, "未知")
        updated["ping_ze"] = "平" if tone in (1, 2) else "仄" if tone in (3, 4) else "未知"
        updated["source"] = "tier2_pinyin"
        updated["tier_label"] = TIER_LABEL
        out.append(updated)
    return out


def locate(first_line: str, poet: str = "", tune: str = "") -> list[dict]:
    """按首句（+ 作者）定位 Tier 2a 作品，返回候选列表。"""
    key = "".join(ch for ch in (first_line or "") if "一" <= ch <= "鿿")[:8]
    if not key:
        return []
    with _LOCK:
        connection = _connect()
    try:
        rows = connection.execute(
            "SELECT * FROM works WHERE first_key = ? ORDER BY poet LIMIT 20", (key,)
        ).fetchall()
    finally:
        connection.close()
    out = []
    for row in rows:
        if poet and row["poet"] and poet not in row["poet"]:
            continue
        if tune and row["tune"] and tune not in row["tune"]:
            continue
        lines = json.loads(row["lines"] or "[]")
        pinyin_lines = json.loads(row["pinyin_lines"] or "[]")
        out.append(
            {
                "tier": "Tier 2a",
                "tier_label": TIER_LABEL,
                "work_id": row["id"],
                "title": row["title"],
                "poet": row["poet"],
                "dynasty": row["dynasty"],
                "tune": row["tune"],
                "lines": lines,
                "pinyin_lines": pinyin_lines,
                "ping": row["ping"],
                "ze": row["ze"],
                "ze_ratio": row["ze_ratio"],
                "line_count": len(lines),
                "warning": WARNING,
            }
        )
    return out


def chars_for(work: dict, line_index: int) -> list[dict]:
    """取该作品第 line_index 句的逐字读音（同源拼音口径）。"""
    pinyin_lines = work.get("pinyin_lines") or []
    lines = work.get("lines") or []
    if line_index >= len(lines) or line_index >= len(pinyin_lines):
        return []
    return _chars_from_pinyin(lines[line_index], pinyin_lines[line_index])

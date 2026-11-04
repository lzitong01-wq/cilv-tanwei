"""Tier 2a 定位与声律测试（V6.1 §1.7 第三层兜底）。

重点：命中时必须标「非官方口径」，逐字读音来自同源带调拼音，对不齐时不用。
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import parser, tier2  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

INDEX = BACKEND.parent / "data" / "tier2_locator.sqlite3"


@unittest.skipUnless(tier2.available(), "Tier 2 定位索引未生成：先跑 scripts/build_tier2_locator.py")
class Tier2LocatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.store = Store(settings.snapshot_path, settings.index_path)
        connection = sqlite3.connect("file:%s?mode=ro" % INDEX.as_posix(), uri=True)
        try:
            cls.row = connection.execute(
                "SELECT title, poet, lines, pinyin_lines FROM works LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        cls.lines = json.loads(cls.row[2])
        cls.text = "。".join(cls.lines[:2]) + "。"

    def test_locate_finds_work_by_first_line(self):
        hits = tier2.locate(self.lines[0])
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], self.row[0])

    def test_result_is_marked_non_official(self):
        hits = tier2.locate(self.lines[0])
        self.assertIn("非官方口径", hits[0]["tier_label"])
        self.assertIn("非官方口径", hits[0]["warning"])

    def test_chars_use_tier2_pinyin(self):
        """同源拼音能对齐的句子：逐字读音来自同源拼音，不是今音。"""
        connection = sqlite3.connect("file:%s?mode=ro" % INDEX.as_posix(), uri=True)
        try:
            rows = connection.execute("SELECT lines, pinyin_lines FROM works LIMIT 30").fetchall()
        finally:
            connection.close()
        checked = False
        for lines_json, pinyin_json in rows:
            work_lines = json.loads(lines_json)
            work_pinyin = json.loads(pinyin_json)
            for position, (text, pinyin_text) in enumerate(zip(work_lines, work_pinyin)):
                items = tier2._chars_from_pinyin(text, pinyin_text)
                if items:
                    for item in items:
                        self.assertEqual(item["source"], "tier2_pinyin")
                        self.assertIn(item["ping_ze"], ("平", "仄", "未知"))
                    checked = True
                    break
            if checked:
                break
        self.assertTrue(checked, "样本里没有一句能对齐，说明测试样本有问题")

    def test_mismatched_pinyin_is_not_guessed(self):
        """调号数与汉字数对不齐就不给逐字，不用猜。"""
        self.assertEqual(tier2._chars_from_pinyin("白头游子", "bái tóu"), [])
        self.assertEqual(len(tier2._chars_from_pinyin("白头", "bái tóu")), 2)

    def test_parse_falls_back_to_tier2_and_marks_it(self):
        result = parser.parse(self.store, self.text)
        located = result["located"]
        self.assertIsNotNone(located)
        self.assertEqual(located["tier"], "Tier 2a")
        self.assertFalse(located["official"])
        self.assertIn("非官方口径", result["tier_label"])
        self.assertIn("非官方口径", result["boundary"])

    def test_tier1_hit_stays_official(self):
        """官方库里的作品仍走 Tier 1，不被 Tier 2 抢走（用完整正文比对）。"""
        snapshot = BACKEND.parent.parent / "data" / "知识库审计快照V1.jsonl"
        official = None
        for line in snapshot.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record["id"] == 58633:
                official = record["text"]
                break
        self.assertIsNotNone(official)
        located = parser.locate(self.store, official)
        self.assertIsNotNone(located)
        self.assertEqual(located["tier"], "Tier 1")
        self.assertTrue(located["official"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

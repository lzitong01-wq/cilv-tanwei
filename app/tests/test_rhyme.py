"""韵脚（句末字 + 《词林正韵》韵部）的回归用例。

为什么要有这组用例：
- 榜题要求研究者路径「输入词作自动输出逐字平仄、四声、**韵脚**」；
  早期只交出了平仄与四声，韵脚缺位。
- 韵部必须来自字表派生事实，查不到就**显式未知**，不能猜一个填上去——
  这是 INV-UNK-05「未知一律显式呈现」在韵脚上的落点。
"""

import sys
import unittest
import urllib.parse
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import create_app  # noqa: E402
from core import rhyme  # noqa: E402
from core.config import load_settings  # noqa: E402


class RhymeUnitTest(unittest.TestCase):
    def test_line_end_char_skips_punctuation(self):
        info = rhyme.line_end_info("寒蝉凄切，对长亭晚，骤雨初歇。")
        self.assertEqual(info["line_end_char"], "歇")
        self.assertTrue(info["rhyme_found"])
        self.assertTrue(info["rhyme"])

    def test_rhyme_group_comes_from_table(self):
        # 《词林正韵》第十八部：入声月曷屑叶同部，「歇」「发」应同部
        self.assertEqual(rhyme.line_end_info("骤雨初歇。")["rhyme"], "第十八部")
        self.assertEqual(rhyme.line_end_info("兰舟催发")["rhyme"], "第十八部")

    def test_unknown_char_is_explicit(self):
        info = rhyme.line_end_info("abcXYZ123")
        self.assertIsNone(info["line_end_char"])
        self.assertIsNone(info["rhyme"])
        self.assertFalse(info["rhyme_found"])
        self.assertIn("没有可识别", info["note"])

    def test_unlisted_hanzi_is_not_guessed(self):
        info = rhyme.line_end_info("𠮷")
        self.assertFalse(info["rhyme_found"])
        self.assertIsNone(info["rhyme"])
        self.assertIn("不猜测", info["note"])

    def test_annotate_lines_preserves_original_fields(self):
        rows = [{"line_no": 1, "text": "桂丛香到枕。", "ping_count": 2}]
        out = rhyme.annotate_lines(rows)
        self.assertEqual(out[0]["line_no"], 1)
        self.assertEqual(out[0]["ping_count"], 2)
        self.assertEqual(out[0]["line_end"]["line_end_char"], "枕")
        self.assertEqual(rows[0].get("line_end"), None)  # 不改原对象

    def test_boundary_text_is_present(self):
        self.assertIn("与词谱韵位的校勘结论", rhyme.boundary())


class RhymeEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app(load_settings()).test_client()

    def test_parse_returns_line_end_and_boundary(self):
        body = self.client.post(
            "/api/parse", json={"text": "寒蝉凄切，对长亭晚，骤雨初歇。都门帐饮无绪，留恋处，兰舟催发。"}
        ).get_json()["data"]
        self.assertIn("rhyme_boundary", body)
        self.assertTrue(body["lines"])
        for line in body["lines"]:
            self.assertIn("line_end", line)
            self.assertIn("rhyme_found", line["line_end"])
        self.assertEqual(body["lines"][0]["line_end"]["line_end_char"], "歇")

    def test_official_chars_returns_line_end(self):
        body = self.client.get("/api/works/58633/chars").get_json()["data"]
        self.assertIn("rhyme_boundary", body)
        self.assertEqual(body["lines"][0]["line_end"]["line_end_char"], "枕")

    def test_tier2_chars_returns_line_end(self):
        listing = self.client.get("/api/works?tier=2c&page_size=1").get_json()["data"]
        if not listing["items"]:
            self.skipTest("Tier 2c 索引未就位")
        work_id = listing["items"][0]["id"]
        body = self.client.get("/api/works/%s/chars" % urllib.parse.quote(str(work_id))).get_json()["data"]
        self.assertTrue(body["lines"])
        self.assertIn("line_end", body["lines"][0])

    def test_works_lines_returns_line_end(self):
        body = self.client.get("/api/works/58633/lines").get_json()["data"]
        self.assertIn("rhyme_boundary", body)
        self.assertEqual(body["lines"][0]["line_end"]["line_end_char"], "枕")


if __name__ == "__main__":
    unittest.main(verbosity=2)

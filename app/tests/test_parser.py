"""自由文本解析测试（板块 A）：口径、定位、体式候选、超界与端点。

重点不是"解析得多准"，而是**不冒充官方口径、不静默归体、不隐藏未知字**——
对应 V6.1 §1.5 超界题规则与 §13.1 案例①。
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import engine, parser  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402
from core.store import Store  # noqa: E402

SNAPSHOT = BACKEND.parent.parent / "data" / "知识库审计快照V1.jsonl"
WORK_ID = 58633  # 郑文焯《瑞鹤仙》，与 20294 赵彦端同调同句数但句式不同


def work_text() -> str:
    for line in SNAPSHOT.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["id"] == WORK_ID:
            return record["text"]
    raise AssertionError("快照里没有 58633")


class FreeTextParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.store = Store(settings.snapshot_path, settings.index_path)
        cls.text = work_text()

    def test_split_keeps_inner_commas(self):
        lines = parser.split_lines("桂丛香到枕。渐透帘蛩语，凉烟催暝。")
        self.assertEqual(lines, ["桂丛香到枕", "渐透帘蛩语，凉烟催暝"])

    def test_pattern_matches_official_prosody_for_library_work(self):
        result = parser.parse(self.store, self.text, "瑞鹤仙")
        _, official = engine._prosody_lines(self.store.get(WORK_ID))
        self.assertEqual(tuple(result["pattern"]), tuple(line["character_count"] for line in official))

    def test_located_work_points_back_to_official_page(self):
        result = parser.parse(self.store, self.text)
        self.assertIsNotNone(result["located"])
        self.assertEqual(result["located"]["work_id"], WORK_ID)
        self.assertIn("官方", result["located"]["hint"])

    def test_same_tune_same_line_count_is_ambiguous(self):
        """案例①：瑞鹤仙在库内有 2 种句式，必须明确'不能唯一定体'。"""
        tunes = parser.tune_candidates(self.store, (5, 9, 5, 9, 11, 13, 2, 12, 4, 6, 15, 11), "瑞鹤仙")
        self.assertTrue(tunes["ambiguous"])
        self.assertEqual(tunes["hint_form_count"], 2)
        self.assertIn("不能唯一定体", tunes["boundary"])
        self.assertEqual(tunes["candidates"][0]["score"], 1.0)
        self.assertIn(WORK_ID, tunes["candidates"][0]["sample_work_ids"])

    def test_single_line_stays_unresolved(self):
        """案例②：单句只给逐字结果，不硬凑一个词牌。"""
        result = parser.parse(self.store, "谁念西风独自凉。")
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["lines"][0]["character_count"], 7)
        self.assertEqual(result["lines"][0]["ping_count"] + result["lines"][0]["ze_count"], 7)
        self.assertFalse([c for c in result["tunes"]["candidates"] if c["score"] == 1.0])
        self.assertIn("不能作为官方口径", result["boundary"])
        self.assertIn("非官方口径", result["tier_label"])

    def test_tier_and_boundary_are_explicit(self):
        result = parser.parse(self.store, self.text)
        self.assertIn("非官方口径", result["tier_label"])
        self.assertIn("不是词谱校勘", result["boundary"])
        self.assertIn("不能作为官方口径", result["boundary"])

    def test_unknown_char_is_reported_not_defaulted(self):
        """扩展区汉字不能被静默丢弃：查得到读音就标读音，查不到就显式标未知。"""
        result = parser.parse(self.store, "桂丛香到枕。𨓦𪛖。")
        second = result["lines"][1]
        self.assertEqual([item["char"] for item in second["chars"]], ["𨓦", "𪛖"])
        self.assertNotEqual(second["chars"][0]["ping_ze"], "未知")  # 字典认识 𨓦
        self.assertEqual(second["chars"][1]["ping_ze"], "未知")
        self.assertTrue(second["chars"][1]["unknown_reason"])
        self.assertEqual(result["unknown_count"], 1)
        self.assertEqual(result["unknown"][0]["char"], "𪛖")

    def test_empty_and_oversize_are_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            parser.parse(self.store, "")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        with self.assertRaises(ApiError) as ctx:
            parser.parse(self.store, "春。\n" * 45)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_save_get_and_expiry(self):
        result = parser.parse(self.store, self.text, "瑞鹤仙")
        parse_id = parser.save(result)
        self.assertTrue(parse_id.startswith("parse_"))
        self.assertEqual(parser.get(parse_id)["pattern"], result["pattern"])
        with self.assertRaises(ApiError) as ctx:
            parser.get("parse_not_exist")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")


class ParseEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.client = create_app(load_settings()).test_client()

    def test_post_and_get(self):
        response = self.client.post("/api/parse", json={"text": work_text(), "tune": "瑞鹤仙"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        data = body["data"]
        self.assertTrue(data["parse_id"])
        self.assertTrue(data["tunes"]["ambiguous"])
        got = self.client.get("/api/parse/" + data["parse_id"] + "/tunes").get_json()
        self.assertTrue(got["ok"])
        self.assertEqual(got["data"]["hint_form_count"], 2)

    def test_empty_text_returns_400(self):
        response = self.client.post("/api/parse", json={"text": ""})
        self.assertEqual(response.status_code, 400)

    def test_unknown_parse_id_returns_404(self):
        self.assertEqual(self.client.get("/api/parse/parse_nope/tunes").status_code, 404)


class FreeTextBoundaryTest(unittest.TestCase):
    """M1 完成定义要求的边界用例：分句、字数、未知字、定位、体式候选、越界。"""

    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.store = Store(settings.snapshot_path, settings.index_path)
        cls.text = work_text()

    # —— 分句边界 ——

    def test_question_and_exclamation_split(self):
        self.assertEqual(len(parser.split_lines("问句？叹句！")), 2)

    def test_consecutive_punctuation_makes_no_empty_sentence(self):
        self.assertEqual(parser.split_lines("桂丛香到枕。。！渐透帘蛩语。"), ["桂丛香到枕", "渐透帘蛩语"])

    def test_newline_splits_sentences(self):
        self.assertEqual(parser.split_lines("桂丛香到枕。\n渐透帘蛩语。"), ["桂丛香到枕", "渐透帘蛩语"])

    def test_only_punctuation_is_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            parser.parse(self.store, "。。。")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_whitespace_only_is_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            parser.parse(self.store, "　 \n\t")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_no_punctuation_counts_as_one_sentence(self):
        result = parser.parse(self.store, "桂丛香到枕渐透帘蛩语")
        self.assertEqual(len(result["lines"]), 1)

    # —— 字数与未知字 ——

    def test_punctuation_not_counted_in_characters(self):
        result = parser.parse(self.store, "桂丛香到枕。")
        self.assertEqual(result["lines"][0]["character_count"], 5)

    def test_unknown_char_not_silently_counted_as_ping_or_ze(self):
        """INV-UNK-05：字典无读音的字要单独报出来，不能默认归平或归仄。"""
        result = parser.parse(self.store, "桂丛香𪛖到枕。")
        line = result["lines"][0]
        self.assertEqual(line["character_count"], 6)
        self.assertLess(line["ping_count"] + line["ze_count"], line["character_count"])
        self.assertEqual(result["unknown_count"], 1)
        self.assertEqual(result["unknown"][0]["char"], "𪛖")
        self.assertTrue(result["unknown"][0]["reason"])

    def test_extension_b_char_with_reading_is_counted(self):
        """扩展区汉字字典里有读音时正常计入平仄，不该一律当未知。"""
        result = parser.parse(self.store, "桂𠀀丛香。")
        items = result["lines"][0]["chars"]
        self.assertEqual(len(items), 4)
        self.assertNotEqual(items[1]["ping_ze"], "未知")
        self.assertEqual(result["unknown_count"], 0)

    def test_extension_b_char_without_reading_is_unknown(self):
        result = parser.parse(self.store, "桂𪛖丛香。")
        self.assertEqual(result["unknown_count"], 1)

    # —— 库内定位 ——

    def test_punctuation_variant_still_locates_same_work(self):
        """标点差异不影响定位：比较的是字面，不含标点。"""
        located = parser.locate(self.store, self.text.replace("。", "，"))
        self.assertIsNotNone(located)
        self.assertEqual(located["work_id"], WORK_ID)

    def test_extra_prefix_does_not_locate(self):
        """多一个字就不认：宁可定位不上，也不硬说它是同一首。"""
        self.assertIsNone(parser.locate(self.store, "甲：" + self.text))

    def test_traditional_variant_does_not_locate(self):
        """繁简不同不算同一首：系统不做繁简归并，避免"看起来一样"就认。"""
        self.assertIsNone(parser.locate(self.store, self.text.replace("凉烟", "涼烟")))

    def test_located_result_points_back_to_official_page(self):
        result = parser.parse(self.store, self.text)
        self.assertIsNotNone(result["located"])
        self.assertIn("官方", result["located"]["hint"])

    # —— 体式候选 ——

    def test_exact_pattern_scores_one(self):
        pattern = tuple(
            line["character_count"]
            for line in engine._prosody_lines(self.store.get(WORK_ID))[1]
        )
        tunes = parser.tune_candidates(self.store, pattern, "瑞鹤仙")
        self.assertEqual(tunes["candidates"][0]["score"], 1.0)
        self.assertEqual(tunes["candidates"][0]["tune"], "瑞鹤仙")

    def test_tune_hint_restricts_candidates(self):
        pattern = (7, 7, 7, 3, 7, 7, 7, 7, 3, 7)
        with_hint = parser.tune_candidates(self.store, pattern, "渔家傲")
        self.assertTrue(with_hint["candidates"])
        for item in with_hint["candidates"]:
            self.assertEqual(item["tune"], "渔家傲")

    def test_unknown_tune_hint_returns_empty_with_boundary(self):
        tunes = parser.tune_candidates(self.store, (7, 7, 7, 3), "不存在的词牌")
        self.assertEqual(tunes["candidates"], [])
        self.assertIn("无法归体", tunes["boundary"])

    def test_far_length_difference_gives_no_candidate(self):
        """句数或字数差太多就不给近似候选，避免"看着像"就归体。"""
        tunes = parser.tune_candidates(self.store, (99, 99, 99), "")
        self.assertEqual(tunes["candidates"], [])

    def test_split_after_is_inferred_not_official(self):
        result = parser.parse(self.store, "甲。\n乙。\n丙。")
        self.assertEqual(result["split_after"], 1)  # max(1, floor(3/2))
        self.assertIn("本地推测", result["source_note"])

    # —— 口径与越界 ——

    def test_result_never_claims_official_annotation(self):
        """本地口径不能自称官方：tier_label 与边界声明都要写明。"""
        result = parser.parse(self.store, "谁念西风独自凉。")
        self.assertEqual(result["tier_label"], "非官方口径 · 本地计算")
        self.assertIn("非官方标注", result["boundary"])

    def test_too_many_sentences_is_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            parser.parse(self.store, "春。\n" * 41)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_endpoint_rejects_too_long_text(self):
        from app import create_app

        client = create_app(load_settings()).test_client()
        response = client.post("/api/parse", json={"text": "春。" * 2000})
        self.assertEqual(response.status_code, 400)

    def test_endpoint_rejects_missing_text(self):
        from app import create_app

        client = create_app(load_settings()).test_client()
        self.assertEqual(client.post("/api/parse", json={}).status_code, 400)

    def test_endpoint_unknown_parse_id_404(self):
        from app import create_app

        client = create_app(load_settings()).test_client()
        self.assertEqual(client.get("/api/parse/parse_nope").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)

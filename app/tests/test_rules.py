"""规则外置测试：验证引擎真的从 rules.json 读判类与口径，不是摆设。"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import engine, rules, sidecar  # noqa: E402
from core.errors import ApiError  # noqa: E402

C5_GOLD_BOUNDARY = (
    "解释边界：景物、感受和声律变化只能构成有限的文本互证，"
    "不能直接证明作者经历、时代因果或作品优劣。"
)
C5_GOLD_STATUS = "语义验证状态：未验证；景物/感受归类及互证解释仅作候选，不作为语义金标准。"


class RulesTest(unittest.TestCase):
    def test_registry_is_complete(self):
        data = rules.load()
        self.assertEqual(data["contract"], "v1")
        self.assertTrue(data["version"])
        self.assertEqual(len(data["categories"]), 5)
        self.assertEqual(engine.IMPLEMENTED, rules.category_names())

    def test_detect_uses_configured_keywords(self):
        self.assertEqual(engine.detect_category("请比较最长句的句序"), rules.category_names()[0])
        self.assertEqual(engine.detect_category("各引一条后段原文"), rules.category_names()[3])
        self.assertEqual(engine.detect_category("景物线索"), rules.category_names()[4])
        with self.assertRaises(ApiError) as ctx:
            engine.detect_category("一句无法归类的话")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_hotfix_keyword_takes_effect_without_code_change(self):
        """模拟热修：往配置里加一条措辞，判类立刻生效。"""
        original = rules.detect
        patched = dict(rules.load())
        patched["categories"] = [
            {**item, "match": {"any": list((item.get("match") or {}).get("any") or [])}}
            for item in patched["categories"]
        ]
        patched["categories"][0]["match"]["any"].append("热修措辞XYZ")
        rules.detect = lambda question: next(
            (item["name"] for item in patched["categories"]
             if any(word in question for word in item["match"]["any"])),
            "",
        )
        try:
            self.assertEqual(engine.detect_category("热修措辞XYZ"), rules.category_names()[0])
        finally:
            rules.detect = original

    def test_detect_supports_all_and_any(self):
        """多关键词规则：all 要全部命中，any 任一命中即可；措辞变体不致漏判。"""
        self.assertEqual(rules.detect("最长句的句序与字数"), rules.category_names()[0])
        self.assertEqual(rules.detect("最长句句序与字数"), rules.category_names()[0])
        self.assertEqual(rules.detect("变化值与绝对变化"), rules.category_names()[1])
        self.assertEqual(rules.detect("各引一条后段原文"), rules.category_names()[3])
        self.assertEqual(rules.detect("景物线索与主观感受"), rules.category_names()[4])
        self.assertEqual(rules.detect("今天天气不错"), "")

    def test_c5_text_matches_gold_verbatim(self):
        self.assertEqual(engine.C5_BOUNDARY, C5_GOLD_BOUNDARY)
        self.assertEqual(engine.C5_STATUS, C5_GOLD_STATUS)
        self.assertEqual(rules.c5_text(), (C5_GOLD_BOUNDARY, C5_GOLD_STATUS))

    def test_gate_wordlist_and_model_profiles_come_from_rules(self):
        self.assertEqual(sidecar.BOUNDARY_CLAIMS, rules.boundary_claims())
        self.assertEqual(sidecar.MODEL_PROFILES, rules.model_profiles())
        self.assertEqual(sidecar.DEFAULT_MODEL, rules.load()["default_model"])
        self.assertIn("艺术价值更高", sidecar.BOUNDARY_CLAIMS)

    def test_rules_endpoint(self):
        from app import create_app
        from core.config import load_settings

        client = create_app(load_settings()).test_client()
        response = client.get("/api/rules")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["version"], rules.load()["version"])

    def test_view_flattens_match_into_detect(self):
        """视图要把 match/detect 摊平成关键词列表。

        界面直接渲染 `detect`；2026-11-04「语料与授权」白屏就是因为
        规则升级成 `match: {"all": [...]}` 后，接口不再给 detect，
        前端 `item.detect.join()` 抛异常、整棵树被卸载。
        """
        view = rules.view()
        for item in view["categories"]:
            self.assertIsInstance(item["detect"], list, "detect 必须是列表：%r" % item)
            self.assertTrue(item["detect"], "关键词不能为空：%r" % item)
            self.assertIn(item["match_mode"], ("all", "any"))

    def test_view_detect_matches_configured_keywords(self):
        """摊平后的关键词必须与配置里的实际生效词一致。"""
        view = rules.view()
        for raw, item in zip(rules.load()["categories"], view["categories"]):
            keywords, mode = rules.match_keywords(raw)
            self.assertEqual(item["detect"], keywords)
            self.assertEqual(item["match_mode"], mode)
            for keyword in keywords:
                self.assertIn(keyword, raw.get("match", {}).get(mode, []) or raw.get("detect") or [])

    def test_rules_endpoint_serves_flat_keywords(self):
        """接口层也要摊平——前端拿到的就是这份。"""
        from app import create_app
        from core.config import load_settings

        client = create_app(load_settings()).test_client()
        body = client.get("/api/rules").get_json()
        categories = body["data"]["categories"]
        self.assertEqual(len(categories), 5)
        first = categories[0]
        self.assertIsInstance(first["detect"], list)
        self.assertTrue(first["detect"])
        self.assertEqual(first["match_mode"], "all")
        self.assertIn("最长句", first["detect"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

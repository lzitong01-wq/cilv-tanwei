"""多音字修正表测试（分册 A §A5）：规则形态、应用语义与门禁口径。"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import polyphone  # noqa: E402

APP = BACKEND.parent
RULES_FILE = APP / "data" / "polyphone_rules.json"


class PolyphoneRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RULES_FILE.is_file():
            raise unittest.SkipTest("修正表未生成：先跑 scripts/build_polyphone_rules.py")
        cls.payload = json.loads(RULES_FILE.read_text(encoding="utf-8"))

    def test_every_rule_carries_context(self):
        """A5.2：规则按「字 + 上下文」表达，禁止纯字硬编码。"""
        for rule in self.payload["rules"]:
            self.assertTrue(rule["prev"], "规则缺少左侧上下文")
            self.assertTrue(rule["next"], "规则缺少右侧上下文")
            self.assertNotEqual(rule["prev"], "*")
            self.assertNotEqual(rule["next"], "*")
            self.assertIn("status", rule)

    def test_char_level_rules_are_marked_as_fallback(self):
        for rule in self.payload["char_rules"]:
            self.assertEqual(rule["prev"], "*")
            self.assertEqual(rule["next"], "*")
            self.assertEqual(rule["status"], "fallback")

    def test_conflicts_are_kept_not_forced(self):
        for conflict in self.payload["conflicts"]:
            self.assertGreater(len(conflict["options"]), 1)
            self.assertIn("无法判定", conflict["note"])

    def test_metrics_are_reported_honestly(self):
        metrics = self.payload["metrics"]
        self.assertEqual(metrics["diff_lines"], 299)
        self.assertIn("coverage_pass", metrics)
        self.assertIn("official_fix_rate", metrics)
        self.assertIn("tier2_pass", metrics)
        # 一级未达标时必须如实标 false，不能改成 true
        if metrics["official_fix_rate"] < 0.95:
            self.assertFalse(metrics["coverage_pass"])


class ApplyRulesTest(unittest.TestCase):
    def test_at_most_one_char_changed_per_sentence(self):
        """多字命中时不整体应用：否则会互相抵消，把句子改坏。"""
        rules = [
            {"char": "长", "prev": "*", "next": "*", "reading": "chang2"},
            {"char": "别", "prev": "*", "next": "*", "reading": "bie2"},
        ]
        result = polyphone.apply_rules("长别", rules)
        self.assertLessEqual(len(result["hits"]), 1)
        self.assertTrue(result["ambiguous"])

    def test_target_ping_selects_the_rule_that_fits(self):
        rules = [
            {"char": "长", "prev": "*", "next": "*", "reading": "chang2"},
            {"char": "别", "prev": "*", "next": "*", "reading": "bie2"},
        ]
        # "长别"：长=平、别=平 → 平 2。要变成平 1，得把其中一个改成仄
        target = 1
        result = polyphone.apply_rules("长别", rules, target_ping=target)
        self.assertEqual(len(result["hits"]), 1)
        ping = sum(1 for item in result["items"] if item["ping_ze"] == "平")
        self.assertEqual(ping, target)

    def test_exact_context_wins_over_fallback(self):
        rules = [
            {"char": "长", "prev": "^", "next": "句", "reading": "zhang3"},
            {"char": "长", "prev": "*", "next": "*", "reading": "chang2"},
        ]
        result = polyphone.apply_rules("长句", rules)
        self.assertEqual(result["hits"][0]["reading"], "zhang3")
        self.assertTrue(result["hits"][0]["exact"])

    def test_fallback_used_when_no_exact_match(self):
        rules = [{"char": "长", "prev": "*", "next": "*", "reading": "chang2"}]
        result = polyphone.apply_rules("青山长句", rules)
        self.assertEqual(len(result["hits"]), 1)
        self.assertFalse(result["hits"][0]["exact"])

    def test_solve_line_requires_candidate_polyphones(self):
        """不在候选里的字不许改判。"""
        solutions = polyphone.solve_line("桂丛香到枕。", ["香"], 2, 3)
        self.assertEqual(solutions, [])

    def test_ping_ze_of_without_rules_is_plain_local_count(self):
        self.assertEqual(polyphone.ping_ze_of("桂丛香到枕。"), (2, 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)

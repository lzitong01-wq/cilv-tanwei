"""情感候选与基准集逻辑测试（板块 G / ADR-0006）。

不依赖真模型：用 mock caller 覆盖合法、越界、结构非法三种情形，
并验证 κ 计算、标签闭集、禁止推断拦截与端点契约。
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import sentiment  # noqa: E402
from core.config import load_settings  # noqa: E402

SENTENCE = {
    "id": "w58633-l2",
    "text": "渐透帘蛩语，凉烟催暝。",
    "work_id": 58633,
    "title": "瑞鹤仙",
    "tune": "瑞鹤仙",
    "poet": "郑文焯",
    "period": "清",
    "line_no": 2,
    "chars": 9,
    "ping": 6,
    "ze": 3,
}


def mock_caller(payload: str, ok: bool = True):
    def caller(messages, model, timeout):
        return {"ok": ok, "content": payload, "latency_ms": 12, "model": model}

    return caller


class SentimentLogicTest(unittest.TestCase):
    def test_label_set_is_closed(self):
        self.assertEqual(len(sentiment.LABELS), 9)
        self.assertIn("无法判定", sentiment.LABELS)
        self.assertEqual(len(set(sentiment.LABELS)), len(sentiment.LABELS))

    def test_cohen_kappa_known_values(self):
        self.assertEqual(sentiment.cohen_kappa(["a", "a", "b"], ["a", "a", "b"]), 1.0)
        self.assertLess(sentiment.cohen_kappa(["a", "b", "c"], ["x", "y", "z"]), 0.5)
        self.assertEqual(sentiment.cohen_kappa([], []), 0.0)

    def test_majority_needs_over_half(self):
        self.assertEqual(sentiment.majority(["a", "a", "b"]), ("a", True))
        self.assertEqual(sentiment.majority(["a", "b", "c"]), ("a", False))

    def test_parse_accepts_both_shapes(self):
        flat = json.dumps(
            {"items": [{"id": "s1", "label": "愁苦失意", "strength": 2, "reason": "自陈凄迷", "boundary": "x"}]}
        )
        nested = json.dumps(
            {"items": [{"id": "s2", "draft": {"label": "闲适自得", "strength": 1}, "basis": "句内从容"}]}
        )
        self.assertEqual(sentiment.parse_items(flat, ["s1"])["s1"]["label"], "愁苦失意")
        self.assertEqual(sentiment.parse_items(nested, ["s2"])["s2"]["label"], "闲适自得")

    def test_parse_drops_unknown_ids_and_labels(self):
        payload = json.dumps({"items": [{"id": "zz", "label": "愁苦失意", "strength": 1}]})
        self.assertEqual(sentiment.parse_items(payload, ["s1"]), {})
        payload = json.dumps({"items": [{"id": "s1", "label": "胡编的标签", "strength": 1}]})
        self.assertEqual(sentiment.parse_items(payload, ["s1"]), {})

    def test_inference_patterns_are_caught(self):
        self.assertTrue(sentiment.violates_inference("此为悼亡之作"))
        self.assertTrue(sentiment.violates_inference("作者中年失意"))
        self.assertFalse(sentiment.violates_inference("句内仅写落红与鬓影"))

    def test_fact_card_keeps_official_counts(self):
        card = sentiment.sentence_fact_card(SENTENCE)
        self.assertEqual(card["facts"]["ping"], 6)
        self.assertEqual(card["facts"]["ze"], 3)
        self.assertEqual(card["facts"]["ze_ratio"], 33.3)
        self.assertIn("官方标注", card["tier_label"])


class SentimentCandidateTest(unittest.TestCase):
    def _payload(self, label="愁苦失意", strength=2, reason="句内自陈凄迷", boundary="不能推出作者生平"):
        return json.dumps(
            {"items": [{"id": "w58633-l2", "label": label, "strength": strength,
                        "reason": reason, "boundary": boundary}]},
            ensure_ascii=False,
        )

    def test_legal_candidate_passes_all_gates(self):
        result = sentiment.candidate(SENTENCE, caller=mock_caller(self._payload()))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["direction"], "愁苦失意")
        self.assertEqual(result["strength"], 2)
        self.assertEqual(result["candidate"]["verification"], "未验证")

    def test_inference_is_blocked_by_extra_gate(self):
        result = sentiment.candidate(
            SENTENCE, caller=mock_caller(self._payload(reason="此为悼亡之作"))
        )
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["checks"]["inference"])
        self.assertIsNone(result["candidate"])

    def test_invalid_label_fails_structure(self):
        result = sentiment.candidate(SENTENCE, caller=mock_caller(self._payload(label="胡说八道")))
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["checks"]["structure"])

    def test_model_failure_degrades(self):
        result = sentiment.candidate(SENTENCE, caller=mock_caller("", ok=False))
        self.assertEqual(result["status"], "degraded")
        self.assertIsNone(result["candidate"])
        self.assertIsNotNone(result["fact_card"])


class SentimentEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.client = create_app(load_settings()).test_client()

    def test_candidate_requires_input(self):
        self.assertEqual(self.client.post("/api/sentiment/candidate", json={}).status_code, 400)

    def test_candidate_unknown_work_returns_404(self):
        response = self.client.post("/api/sentiment/candidate", json={"work_id": 999999, "line_no": 1})
        self.assertEqual(response.status_code, 404)

    def test_benchmark_endpoint_reports_missing_file(self):
        response = self.client.get("/api/sentiment/benchmark")
        self.assertIn(response.status_code, (200, 404))
        if response.status_code == 404:
            body = response.get_json()
            self.assertFalse(body["ok"])
            self.assertEqual(body["error"]["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main(verbosity=2)

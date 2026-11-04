"""副功能闸门测试：四道闸门各配正反用例，模型用 mock 注入。

设计要点：闸门的价值在于「该拦的必须拦住」，所以每个闸门都要有负向用例；
正向用例保证没有误杀。真实模型端到端单独标记，默认跳过。
"""

import json
import os
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import sidecar  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

WORK_ID = 58633


def fake_caller(content: str, ok: bool = True, error: str | None = None):
    def caller(messages, model, timeout):
        return {
            "ok": ok,
            "content": content,
            "latency_ms": 12,
            "model": model,
            "error": error,
        }

    return caller


class SidecarGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.store = Store(settings.snapshot_path, settings.index_path)
        cls.card = sidecar.build_fact_card(cls.store, WORK_ID)
        facts = cls.card["facts"]
        cls.legal_draft = "这首词共%s句，平%s、仄%s，仄声比例%s%%。" % (
            facts["lines"], facts["ping"], facts["ze"], facts["ze_ratio"]
        )

    def payload(self, draft=None, verification="未验证", boundary="不能据此判定作者意图"):
        return json.dumps(
            {
                "draft": draft if draft is not None else self.legal_draft,
                "basis": ["facts.lines", "facts.ping", "facts.ze", "facts.ze_ratio"],
                "verification": verification,
                "boundary": boundary,
            },
            ensure_ascii=False,
        )

    def run_generate(self, content, ok=True, error=None):
        return sidecar.generate(
            self.store, WORK_ID, caller=fake_caller(content, ok=ok, error=error)
        )

    def test_legal_candidate_passes_all_gates(self):
        result = self.run_generate(self.payload())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["candidate"]["verification"], "未验证")

    def test_fenced_json_is_accepted(self):
        content = "```json\n" + self.payload() + "\n```"
        result = self.run_generate(content)
        self.assertTrue(result["checks"]["structure"])
        self.assertEqual(result["status"], "ok")

    def test_invented_number_is_blocked(self):
        result = self.run_generate(self.payload(draft=self.legal_draft + "全篇共 999 句。"))
        self.assertFalse(result["checks"]["facts"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("999", result["violations"]["facts"])

    def test_value_judgement_is_blocked(self):
        result = self.run_generate(self.payload(draft=self.legal_draft + "该词艺术价值更高。"))
        self.assertFalse(result["checks"]["boundary"])
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["violations"]["boundary"])

    def test_missing_unverified_label_is_blocked(self):
        result = self.run_generate(self.payload(verification="已验证"))
        self.assertFalse(result["checks"]["label"])
        self.assertEqual(result["status"], "blocked")

    def test_non_json_is_blocked(self):
        result = self.run_generate("这首词写得很好，平仄均衡。")
        self.assertFalse(result["checks"]["structure"])
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["candidate"]["verification"])

    def test_model_failure_degrades_without_fabrication(self):
        result = self.run_generate("", ok=False, error="timeout")
        self.assertEqual(result["status"], "degraded")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["fact_card"]["work"]["id"], WORK_ID)
        self.assertIn("timeout", result["fallback_reason"])

    def test_unknown_model_rejected(self):
        from core.errors import ApiError

        with self.assertRaises(ApiError):
            sidecar.generate(self.store, WORK_ID, model="gpt-4o")

    @unittest.skipUnless(os.environ.get("RUN_MODEL_E2E") == "1", "需要内网模型端点，设 RUN_MODEL_E2E=1 开启")
    def test_real_model_end_to_end(self):
        result = sidecar.generate(self.store, WORK_ID, question="这首词的形式特征是什么？")
        self.assertIn(result["status"], ("ok", "blocked", "degraded"))
        self.assertIsNotNone(result["fact_card"])
        if result["status"] == "ok":
            self.assertTrue(all(result["checks"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)

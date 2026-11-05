"""论证辅助**生成**的回归用例。

早期该页只把证据卡拼成模板，页面自己写着「未经模型生成」；
榜题要的是「论证辅助生成」。本组用例锁住：
- 只把**用户勾选的证据**交给模型（不给模型自由发挥的余地）；
- 走 `sidecar.guarded()` 四道闸门，结果标「未验证」；
- 模型不可用时返回 degraded，不伪装成模型草稿；
- 缺主张 / 缺勾选证据直接拒，不静默生成。
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import create_app  # noqa: E402
from core import argument as argument_core  # noqa: E402
from core import sidecar  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

WORK_ID = 58633


def fake_caller(content: str, ok: bool = True):
    def caller(messages, model, timeout):
        return {"ok": ok, "content": content, "latency_ms": 12, "model": model, "error": None}
    return caller


class ArgumentUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.store = Store(cls.settings.snapshot_path, cls.settings.index_path)
        cls.store.load()

    def test_selected_card_keeps_only_chosen_refs(self):
        card = sidecar.build_fact_card(self.store, WORK_ID)
        chosen = ["facts.ze_ratio", "facts.lines"]
        subset = argument_core._selected_card(card, chosen)
        self.assertEqual(set(subset["facts"]), {"ze_ratio", "lines"})
        self.assertEqual(subset["selected_refs"], chosen)
        # 未勾选的字段不能漏给模型
        self.assertNotIn("ping", subset["facts"])

    def test_missing_claim_or_refs_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            argument_core.draft(self.store, WORK_ID, "", ["facts.ze_ratio"])
        self.assertIn("需要给出主张", str(ctx.exception))
        with self.assertRaises(Exception) as ctx2:
            argument_core.draft(self.store, WORK_ID, "这首词句长有变化", [])
        self.assertIn("至少勾选", str(ctx2.exception))

    def test_model_draft_goes_through_gates_and_is_unverified(self):
        content = json.dumps({
            "draft": "就形式特征而言，这首词的最长句句序可作对照。",
            "basis": ["facts.ze_ratio"],
            "verification": "未验证",
            "boundary": "不能推断作者生平与情感归类。",
        }, ensure_ascii=False)
        result = argument_core.draft(
            self.store, WORK_ID, "这首词形式上有对照关系",
            ["facts.ze_ratio"], caller=fake_caller(content), settings=self.settings,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidate"]["verification"], "未验证")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["claim"], "这首词形式上有对照关系")

    def test_fabricated_number_is_blocked(self):
        content = json.dumps({
            "draft": "全篇仄声比例为 99.9%，明显偏高。",
            "basis": ["facts.ze_ratio"],
            "verification": "未验证",
            "boundary": "不能推断作者生平与情感归类。",
        }, ensure_ascii=False)
        result = argument_core.draft(
            self.store, WORK_ID, "这首词形式上有对照关系",
            ["facts.ze_ratio"], caller=fake_caller(content), settings=self.settings,
        )
        self.assertFalse(result["checks"]["facts"])

    def test_model_unavailable_is_degraded_not_faked(self):
        result = argument_core.draft(
            self.store, WORK_ID, "这首词形式上有对照关系",
            ["facts.ze_ratio"], caller=fake_caller("", ok=False), settings=self.settings,
        )
        self.assertEqual(result["status"], "degraded")
        self.assertIsNone(result["candidate"])


class ArgumentEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app(load_settings()).test_client()

    def test_endpoint_rejects_empty_refs(self):
        response = self.client.post(
            "/api/argument/draft",
            json={"work_id": WORK_ID, "claim": "一首词", "refs": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main(verbosity=2)

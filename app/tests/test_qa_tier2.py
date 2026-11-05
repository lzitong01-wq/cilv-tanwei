"""问词异步任务的同源层回归。

线上实测缺陷：选择「同源宋词」作品后，事实卡正常，但后台候选任务抛
`INTERNAL: ValueError("invalid literal for int() with base 10: 't2b_...'")`。
根因是 `qa.start` 起线程时漏传 settings，`sidecar.generate` 回退到官方快照
的 `store.get()`，对字符串 id 做 int() 转换。本组用例锁住不再复发。
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import create_app  # noqa: E402
from core import corpus, qa, sidecar, store as store_mod  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402

DEGRADED = {
    "status": "degraded",
    "candidate": None,
    "checks": None,
    "model": None,
    "fallback_reason": "测试替身：不连真实模型",
}


def wait_job(job_id: str, limit: int = 200) -> dict:
    job = None
    for _ in range(limit):
        job = qa.get(job_id)
        if job["state"] != "running":
            break
        time.sleep(0.01)
    return job


class QaTier2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.client = create_app(cls.settings).test_client()
        cls.store = store_mod.Store(cls.settings.snapshot_path, cls.settings.index_path)

    def _tier2_id(self):
        if not corpus.tier2_available(self.settings):
            self.skipTest("Tier 2 索引未就位")
        block = corpus.list_tier2(self.settings, tier="2b", page=1, page_size=1)
        if not block["items"]:
            self.skipTest("同源宋词层未就位")
        return block["items"][0]["id"]

    def test_async_task_receives_settings(self):
        """qa.start 起线程必须把 settings 一路传到 sidecar.generate。"""
        seen = {}

        def fake_generate(*_args, **kwargs):
            seen["settings"] = kwargs.get("settings")
            return dict(DEGRADED, model=kwargs.get("model"))

        with patch.object(sidecar, "generate", side_effect=fake_generate):
            started = qa.start(
                self.store, "t2b_dummy", "这首词有什么特点？",
                sidecar.DEFAULT_MODEL, self.settings,
            )
            job = wait_job(started["qa_id"])
        self.assertEqual(job["state"], "done")
        self.assertIs(seen["settings"], self.settings)

    def test_qa_candidates_no_valueerror_for_tier2(self):
        """端到端：同源层 id 发起问词，轮询结果不得是 INTERNAL ValueError。"""
        work_id = self._tier2_id()
        with patch.object(sidecar, "generate", return_value=dict(DEGRADED)):
            response = self.client.post(
                "/api/qa", json={"work_id": work_id, "question": "这首词有什么特点？"}
            )
            self.assertEqual(response.status_code, 202)
            card = response.get_json()["data"]["fact_card"]
            self.assertIn("非官方", card["tier_label"])
            qa_id = response.get_json()["data"]["qa_id"]
            data = None
            for _ in range(200):
                data = self.client.get(f"/api/qa/{qa_id}/candidates").get_json()["data"]
                if data["state"] != "running":
                    break
                time.sleep(0.01)
        self.assertNotEqual(data["state"], "failed", data.get("error"))
        self.assertNotIn("ValueError", str(data.get("error")))

    def test_flash_model_is_accepted(self):
        """「换 flash」用的模型名必须在白名单里，异步任务同样能跑完。"""
        self.assertIn(sidecar.SECOND_MODEL, sidecar.MODEL_PROFILES)
        with patch.object(sidecar, "generate", return_value=dict(DEGRADED, model=sidecar.SECOND_MODEL)):
            response = self.client.post(
                "/api/qa",
                json={"work_id": self._tier2_id(), "question": "这首词有什么特点？",
                      "model": sidecar.SECOND_MODEL},
            )
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.get_json()["data"]["model"], sidecar.SECOND_MODEL)
            qa_id = response.get_json()["data"]["qa_id"]
            data = None
            for _ in range(200):
                data = self.client.get(f"/api/qa/{qa_id}/candidates").get_json()["data"]
                if data["state"] != "running":
                    break
                time.sleep(0.01)
        self.assertNotEqual(data["state"], "failed", data.get("error"))

    def test_store_rejects_string_id_with_clear_error(self):
        """防御：即使还有路径把同源层 id 传给官方快照，也只给可读的 INVALID_INPUT。"""
        with self.assertRaises(ApiError) as ctx:
            self.store.get("t2b_not_an_int")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()

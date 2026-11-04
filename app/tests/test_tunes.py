"""词调身份层测试（分册 A §A4）：唯一命中、业务键、别名归并与复核记录。"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import tunes  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402
from core.store import Store  # noqa: E402


class TuneIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.store = Store(settings.snapshot_path, settings.index_path)
        cls.data = tunes.build(cls.store)

    def test_unique_tune_hit_is_100_percent(self):
        """A4.4 门禁：423 篇必须 100% 命中唯一词调（可多体，不可多调）。"""
        stats = self.data["stats"]
        self.assertEqual(stats["unique_tune_rate"], 1.0)
        self.assertEqual(stats["missing_tune"], [])
        self.assertEqual(stats["works"], 423)
        self.assertEqual(stats["tunes"], 100)

    def test_multi_form_tunes_are_kept_not_merged(self):
        """同调异体必须并存（瑞鹤仙有 2 种句式）。"""
        stats = self.data["stats"]
        self.assertGreater(stats["multi_form_tunes"], 0)
        tune = tunes.get(self.store, "tune_瑞鹤仙")
        self.assertEqual(len(tune["forms"]), 2)
        ids = sorted({wid for form in tune["forms"] for wid in form["work_ids"]})
        self.assertEqual(ids, [20294, 58633])

    def test_business_key_is_tune_poet_total(self):
        tune = tunes.get(self.store, "tune_瑞鹤仙")
        form = next(item for item in tune["forms"] if 58633 in item["work_ids"])
        self.assertEqual(form["business_key"], "瑞鹤仙|郑文焯|102")
        self.assertTrue(form["form_id"].startswith("form_"))
        self.assertEqual(len(form["form_id"]), len("form_") + 12)

    def test_form_id_is_stable_not_order_dependent(self):
        """内部 ID 取内容哈希：排序变化不应改变 ID。"""
        first = {form["form_id"] for form in tunes.get(self.store, "tune_瑞鹤仙")["forms"]}
        tunes._CACHE.clear()
        second = {form["form_id"] for form in tunes.get(self.store, "tune_瑞鹤仙")["forms"]}
        self.assertEqual(first, second)

    def test_alias_merge_keeps_unmerged_explicit(self):
        review = tunes.review_sample(self.store)
        self.assertEqual(review["size"], 50)
        self.assertEqual(review["seed"], 20261104)
        for record in review["items"]:
            self.assertTrue(record["rule"])
            self.assertIn("merged", record)

    def test_resolve_returns_all_hits(self):
        result = tunes.resolve(self.store, "瑞鹤仙")
        self.assertGreaterEqual(result["total"], 2)
        self.assertIn("不静默取第一条", result["boundary"])

    def test_unknown_tune_returns_404(self):
        with self.assertRaises(ApiError) as ctx:
            tunes.get(self.store, "tune_不存在的词牌")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_boundary_says_not_prosody_collation(self):
        self.assertIn("不是词谱校勘", self.data["boundary"])


class TuneEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.client = create_app(load_settings()).test_client()

    def test_overview_endpoint(self):
        response = self.client.get("/api/tunes")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["stats"]["tunes"], 100)
        self.assertGreater(len(data["tunes"]), 0)

    def test_detail_and_review_endpoints(self):
        detail = self.client.get("/api/tunes/tune_瑞鹤仙")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.get_json()["data"]["forms"]), 2)
        review = self.client.get("/api/tunes/review")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.get_json()["data"]["size"], 50)

    def test_unknown_tune_returns_404(self):
        self.assertEqual(self.client.get("/api/tunes/tune_不存在的词牌").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)

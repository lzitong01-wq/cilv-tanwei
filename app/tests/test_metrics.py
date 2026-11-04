"""指标汇总测试（分册 D §D1）：三张表互不替代，且未达标项如实列出。"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import metrics  # noqa: E402
from core.config import load_settings  # noqa: E402


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.data = metrics.collect(cls.settings)

    def test_tables_come_from_real_runs(self):
        """指标必须来自实跑产物；没有产物就空着，不能编数字。"""
        if self.data["missing"]:
            self.skipTest("指标产物未生成：%s" % "、".join(self.data["missing"]))
        self.assertTrue(self.data["tables"])
        for row in self.data["tables"]:
            self.assertIsNotNone(row["questions"])
            self.assertIsNotNone(row["proxy"])
            self.assertEqual(row["errors"], 0)

    def test_three_metrics_are_not_merged(self):
        """整段 / 归一化 / 代理是三个独立数字，不能合并成单一准确率。"""
        if not self.data["tables"]:
            self.skipTest("指标产物未生成")
        row = self.data["tables"][0]
        self.assertIsNotNone(row["raw"])
        self.assertIsNotNone(row["normalized"])
        self.assertIsNotNone(row["proxy"])
        self.assertIn("不合并成单一准确率", self.data["boundary"])

    def test_semantic_accuracy_is_not_reported(self):
        gate = next(item for item in self.data["gates"] if item["id"] == "semantic")
        self.assertEqual(gate["level"], "不报告")
        self.assertIn("不报", gate["note"])

    def test_failed_gate_is_still_listed(self):
        """一级门禁未达标也要列出来，不能只留达标的项。"""
        polyphone = next(item for item in self.data["gates"] if item["id"] == "polyphone")
        self.assertIn("一级", polyphone["note"])

    def test_endpoint_returns_payload(self):
        from app import create_app

        client = create_app(load_settings()).test_client()
        response = client.get("/api/metrics")
        self.assertIn(response.status_code, (200, 404))
        if response.status_code == 200:
            body = response.get_json()
            self.assertTrue(body["ok"])
            self.assertIn("gates", body["data"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

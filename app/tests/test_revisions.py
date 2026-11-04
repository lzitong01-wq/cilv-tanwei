"""校订队列测试：用临时副本，不污染真实队列。

覆盖：统计、分页、接受/驳回、重复决策拦截、导出幂等（导出后无新条目即拒绝）。
"""

import shutil
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import revisions  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402


class RevisionTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="rev_")
        source = Path(load_settings().revision_index_path)
        self.db = Path(self.tempdir) / "revisions.sqlite3"
        shutil.copyfile(source, self.db)
        self.settings = replace(load_settings(), revision_index_path=self.db)

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def pending_id(self) -> str:
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute("SELECT id FROM revisions WHERE status='pending' LIMIT 1").fetchone()[0]
        finally:
            connection.close()

    def test_stats(self):
        data = revisions.stats(self.settings)
        self.assertGreaterEqual(data["total"], 1)
        self.assertEqual(data["by_status"]["accepted"], 0)
        self.assertIn("L3", data["closure_level"])

    def test_list_pagination(self):
        page = revisions.list_items(self.settings, page_size=5)
        self.assertEqual(len(page["items"]), 5)
        self.assertGreater(page["total"], 5)
        for item in page["items"]:
            self.assertEqual(item["status"], "pending")
            self.assertTrue(item["reason"])
            self.assertIn("ping", item["local"])
            self.assertIn("ping", item["api"])

    def test_reject_then_not_listed_as_pending(self):
        target = self.pending_id()
        result = revisions.decide(self.settings, target, "reject", "本地读音更可信")
        self.assertEqual(result["status"], "rejected")
        pending_ids = {item["id"] for item in revisions.list_items(self.settings, page_size=100)["items"]}
        self.assertNotIn(target, pending_ids)

    def test_repeat_decision_rejected(self):
        target = self.pending_id()
        revisions.decide(self.settings, target, "accept", "")
        with self.assertRaises(ApiError) as ctx:
            revisions.decide(self.settings, target, "reject", "")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_unknown_id(self):
        with self.assertRaises(ApiError) as ctx:
            revisions.decide(self.settings, "no_such_id", "accept")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_export_is_idempotent(self):
        targets = [self.pending_id()]
        revisions.decide(self.settings, targets[0], "accept", "按官方计数")
        csv_text, key, rows = revisions.export(self.settings)
        self.assertEqual(rows, 1)
        self.assertNotEqual(key, "empty")
        self.assertIn("条目ID", csv_text)
        self.assertIn(targets[0], csv_text)

        again_text, again_key, again_rows = revisions.export(self.settings)
        self.assertEqual(again_rows, 0)
        self.assertEqual(again_key, "empty")
        self.assertEqual(again_text, "")

    def test_export_uses_stable_key(self):
        first = self.pending_id()
        revisions.decide(self.settings, first, "accept")
        _, key_one, _ = revisions.export(self.settings)
        self.assertEqual(len(key_one), 16)

    def test_export_history_records_each_export(self):
        """导出记录：每次成功导出留一条，含幂等键与行数。"""
        self.assertEqual(revisions.export_history(self.settings)["total"], 0)
        revisions.decide(self.settings, self.pending_id(), "accept")
        _, key, rows = revisions.export(self.settings)
        history = revisions.export_history(self.settings)
        self.assertEqual(history["total"], 1)
        record = history["items"][0]
        self.assertEqual(record["export_key"], key)
        self.assertEqual(record["row_count"], rows)
        self.assertEqual(record["filename"], "revisions_%s.csv" % key)
        self.assertIn("L3", history["closure_level"])

    def test_export_history_empty_export_not_recorded(self):
        """没有待导出条目时不应写记录（导出被拒，不是「导出成功」）。"""
        revisions.decide(self.settings, self.pending_id(), "reject")
        _, key, rows = revisions.export(self.settings)
        self.assertEqual(key, "empty")
        self.assertEqual(rows, 0)
        self.assertEqual(revisions.export_history(self.settings)["total"], 0)

    def test_export_history_limit(self):
        revisions.decide(self.settings, self.pending_id(), "accept")
        revisions.export(self.settings)
        history = revisions.export_history(self.settings, limit=1)
        self.assertEqual(len(history["items"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

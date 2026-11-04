"""图谱测试：概览规模、三类焦点、未知焦点、邻居上限。"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import graph  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402
from core.store import Store  # noqa: E402


class GraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.store = Store(settings.snapshot_path, settings.index_path)

    def test_overview(self):
        data = graph.build(self.store)
        self.assertIsNone(data["focus"])
        periods = [node for node in data["nodes"] if node["type"] == "period"]
        self.assertEqual(len(periods), 3)
        self.assertEqual(data["meta"]["works"], 423)
        self.assertIn("史证", data["meta"]["history_layer"])
        self.assertTrue(data["edges"])

    def test_focus_poet(self):
        data = graph.build(self.store, "poet:郑文焯")
        self.assertEqual(data["focus"]["type"], "poet")
        works = [node for node in data["nodes"] if node["type"] == "work"]
        self.assertGreaterEqual(len(works), 1)
        self.assertEqual(data["focus"]["id"], "poet:郑文焯")

    def test_focus_tune(self):
        data = graph.build(self.store, "tune:临江仙")
        self.assertEqual(data["focus"]["type"], "tune")
        self.assertGreaterEqual(data["meta"]["works"], 2)
        self.assertGreaterEqual(data["meta"]["poets"], 2)

    def test_nodes_are_bounded(self):
        data = graph.build(self.store, "tune:临江仙")
        self.assertLessEqual(len(data["nodes"]), graph.MAX_NEIGHBORS + 12)

    def test_unknown_focus_reports_not_found(self):
        with self.assertRaises(ApiError) as ctx:
            graph.build(self.store, "poet:不存在的词人")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_bad_focus_type(self):
        with self.assertRaises(ApiError) as ctx:
            graph.build(self.store, "unknown:xx")
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_history_layer_is_declared_empty_not_fabricated(self):
        """史证层没有数据时必须显式声明为空，且不得凭空生成史证边。"""
        for focus in (None, "poet:郑文焯", "tune:临江仙", "period:清"):
            data = graph.build(self.store, focus)
            self.assertIn("total=0", data["meta"]["history_layer"])
            relations = {edge["relation"] for edge in data["edges"]}
            self.assertFalse(
                {r for r in relations if "史" in r or "生平" in r or "事件" in r},
                "焦点 %r 下出现了史证边" % focus,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""风格画像与对比测试：特征可复算、对照差异、模型解读走同一套闸门。"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import style  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402

LEFT, RIGHT = "汪东", "朱祖谋"


def fake_caller(content: str, ok: bool = True, error: str | None = None):
    def caller(messages, model, timeout):
        return {"ok": ok, "content": content, "latency_ms": 15, "model": model, "error": error}

    return caller


def legal_payload(pair) -> str:
    """用事实卡里真实存在的数字构造合法草稿，避免误判。"""
    a, b = pair["a"], pair["b"]
    return json.dumps(
        {
            "draft": "%s 平均句长 %.2f，%s 平均句长 %.2f，后者句更长。"
            % (a["poet"], a["mean_line_length"], b["poet"], b["mean_line_length"]),
            "basis": ["authors[0].mean_line_length", "authors[1].mean_line_length"],
            "verification": "未验证",
            "boundary": "句长差异不能推出文学风格或价值高低",
        },
        ensure_ascii=False,
    )


class StyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()

    def test_authors_list(self):
        result = style.list_authors(self.settings, limit=5)
        self.assertEqual(len(result["items"]), 5)
        self.assertGreater(result["total"], 100)
        self.assertGreater(result["items"][0]["works"], 0)

    def test_get_style_fields(self):
        data = style.get_style(self.settings, LEFT)
        for field in style.STYLE_FIELDS:
            self.assertIn(field, data)
        self.assertGreaterEqual(data["works"], 20)
        self.assertEqual(data["tier_label"], "公开语料·本地计算")

    def test_unknown_poet_reports_not_found(self):
        with self.assertRaises(ApiError) as ctx:
            style.get_style(self.settings, "不存在的词人")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_compare_has_all_dimensions(self):
        pair = style.compare(self.settings, LEFT, RIGHT)
        self.assertEqual(len(pair["differences"]), len(style.STYLE_FIELDS))
        mean = next(item for item in pair["differences"] if item["field"] == "mean_line_length")
        self.assertEqual(mean["delta"], round(pair["b"]["mean_line_length"] - pair["a"]["mean_line_length"], 2))

    def test_compare_rejects_same_poet(self):
        with self.assertRaises(ApiError):
            style.compare(self.settings, LEFT, LEFT)

    def test_narrative_passes_gates_with_legal_output(self):
        pair = style.compare(self.settings, LEFT, RIGHT)
        result = style.narrative(self.settings, LEFT, RIGHT, caller=fake_caller(legal_payload(pair)))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(all(result["checks"].values()))

    def test_narrative_blocks_value_judgement(self):
        pair = style.compare(self.settings, LEFT, RIGHT)
        payload = json.loads(legal_payload(pair))
        payload["draft"] += "总体艺术价值更高。"
        result = style.narrative(self.settings, LEFT, RIGHT, caller=fake_caller(json.dumps(payload, ensure_ascii=False)))
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["checks"]["boundary"])

    def test_narrative_blocks_invented_number(self):
        pair = style.compare(self.settings, LEFT, RIGHT)
        payload = json.loads(legal_payload(pair))
        payload["draft"] += "另据统计共 9999 首。"
        result = style.narrative(self.settings, LEFT, RIGHT, caller=fake_caller(json.dumps(payload, ensure_ascii=False)))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("9999", result["violations"]["facts"])

    def test_dual_returns_both_models(self):
        result = style.narrative_dual(self.settings, LEFT, RIGHT, caller=fake_caller(legal_payload(style.compare(self.settings, LEFT, RIGHT))))
        self.assertIn("models", result)
        self.assertEqual(set(result["models"]), {"qwen", "flash"})
        for label in ("qwen", "flash"):
            self.assertEqual(result["models"][label]["status"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)

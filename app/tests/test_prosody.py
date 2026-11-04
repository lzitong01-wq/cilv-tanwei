"""词谱体式消歧测试（分册 A §A3）：候选打分、置信度、对齐与门禁。"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import engine, prosody  # noqa: E402

SNAPSHOT = BACKEND.parent.parent / "data" / "知识库审计快照V1.jsonl"


@unittest.skipUnless(prosody.available(), "词谱索引未生成：先跑 scripts/build_prosody_index.py")
class ProsodyDisambiguationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = [
            json.loads(line)
            for line in SNAPSHOT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _candidates(self, work):
        _prosody, lines = engine._prosody_lines(work)
        sentences = [line["character_count"] for line in lines]
        return sentences, prosody.best(
            work.get("tune_pattern") or "",
            sentences,
            work["prosody"].get("split_after_line"),
        )

    def test_candidate_hit_rate_meets_gate(self):
        """A3.3：候选命中率（同字数体式存在）≥85%。"""
        hit = sum(1 for work in self.rows if self._candidates(work)[1] is not None)
        rate = hit / len(self.rows)
        self.assertGreaterEqual(rate, 0.85, "候选命中率 %.1f%% 低于 85%%" % (rate * 100))

    def test_candidates_are_sorted_by_penalty(self):
        for work in self.rows[:60]:
            _sentences, result = self._candidates(work)
            if result is None:
                continue
            penalties = [item["penalty"] for item in result["candidates"]]
            self.assertEqual(penalties, sorted(penalties))

    def test_alignment_only_for_unique_or_high_confidence(self):
        """A3.3：逐字对照仅在唯一/高分时给出（其余一律多候选）。"""
        for work in self.rows:
            _sentences, result = self._candidates(work)
            if result is None:
                continue
            if not result["allowed_for_alignment"]:
                self.assertIsNone(result["form"])
                self.assertFalse(result["unique"])
                continue
            # 允许对照的两种情形：唯一候选，或置信度为高/中
            self.assertTrue(result["unique"] or result["form"]["confidence"] in ("高", "中"))

    def test_align_refuses_mismatched_rhythm(self):
        """句读对不上就不硬凑。"""
        form = {"sentences": [5, 5], "tones": "平平平平平平平平平平"}
        self.assertIsNone(prosody.align(form, [5, 5, 5]))
        self.assertIsNotNone(prosody.align(form, [5, 5]))

    def test_tone_class_counts_only_fixed_tones(self):
        """谱里「中」两可，不计入平也不计入仄。"""
        self.assertEqual(prosody.tone_class("平中仄"), (1, 1))

    def test_unknown_tune_returns_no_candidates(self):
        self.assertEqual(prosody.candidates("不存在的词牌", [7, 7], 1), [])
        self.assertIsNone(prosody.best("不存在的词牌", [7, 7], 1))

    def test_total_chars_is_a_hard_filter(self):
        """总字数不符即出局，不参与打分。"""
        _sentences, result = self._candidates(self.rows[0])
        if result is None:
            self.skipTest("该作品没有同字数体式")
        for item in result["candidates"]:
            self.assertEqual(item["total"], sum(_sentences))


if __name__ == "__main__":
    unittest.main(verbosity=2)

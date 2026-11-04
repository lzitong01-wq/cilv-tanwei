"""官方知识库 API 兜底（可选启用）。

对应赛题的「三层兜底」：本地 Tier 1 快照 → **官方 API 实时查询** → 同源层定位。
只有配置了 `APP_QINGCI_KEY` 才启用；不配置时行为与从前完全一致（离线可用）。

密钥只从环境变量读，**不进代码、不进交付包**。
"""

import os
import sys
import time
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import engine, qingci, store  # noqa: E402
from core.config import load_settings  # noqa: E402

KEY = os.environ.get("APP_QINGCI_KEY", "").strip()
QUESTION = (
    "甲：宋·陈云《谒金门》（词牌“谒金门”，首句“春昼永。”）。\n"
    "乙：宋·史浩《临江仙》（词牌“临江仙”，首句“忆昔来时双髻小，如今云鬓堆鸦。”）。\n"
    "两篇同用该调。请分别给出句数、最长句的句序和字数、全文平仄总数及全文仄声比例；"
    "再计算两篇全文仄声比例相差多少个百分点，并只作形式层面的有限判断。"
)


class ApiDisabledTest(unittest.TestCase):
    """没配密钥时：一切照旧，且绝不发外网请求。"""

    def test_disabled_without_key(self):
        if KEY:
            self.skipTest("本机配置了 APP_QINGCI_KEY")
        self.assertFalse(qingci.enabled())

    def test_local_snapshot_still_works_offline(self):
        settings = load_settings()
        local = store.Store(settings.snapshot_path, settings.index_path)
        result = engine.solve(QUESTION, local)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "Tier 1 官方快照")


@unittest.skipUnless(KEY, "未配置 APP_QINGCI_KEY，跳过官方 API 相关用例")
class ApiFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()

    def _empty_store(self):
        """造一个**本地查不到任何作品**的 store，模拟「官方刚发布的新作品」。"""
        local = store.Store(self.settings.snapshot_path, self.settings.index_path)
        local.load()
        local.works = []
        local.by_id = {}
        local._index = []
        local._loaded = True
        return local

    def test_enabled_with_key(self):
        self.assertTrue(qingci.enabled())

    def test_resolve_work_finds_official_piece(self):
        work = qingci.resolve_work("甲", "陈云", "谒金门", "春昼永。")
        self.assertIsNotNone(work, "官方 API 没能定位到作品")
        self.assertEqual((work.get("poet") or {}).get("name"), "陈云")
        lines = (work.get("prosody") or {}).get("lines") or []
        self.assertTrue(lines, "API 返回的作品没有逐句声律")

    def test_official_piece_matches_local_snapshot_numbers(self):
        """官方 API 与本地快照对同一首作品给出一致的声律数字。"""
        local = store.Store(self.settings.snapshot_path, self.settings.index_path)
        local.load()
        api_work = qingci.resolve_work("甲", "陈云", "谒金门", "春昼永。")
        self.assertIsNotNone(api_work)
        api_lines = (api_work.get("prosody") or {}).get("lines") or []
        api_ping = sum(line["ping_count"] for line in api_lines)
        api_ze = sum(line["ze_count"] for line in api_lines)
        match = None
        for work in local.works:
            if (work.get("poet") or {}).get("name") == "陈云" and "谒金门" in (work.get("title") or ""):
                match = work
                break
        if match is None:
            self.skipTest("本地快照里没有这首作品")
        local_lines = (match.get("prosody") or {}).get("lines") or []
        self.assertEqual(api_ping, sum(line["ping_count"] for line in local_lines))
        self.assertEqual(api_ze, sum(line["ze_count"] for line in local_lines))
        self.assertEqual(len(api_lines), len(local_lines))

    def test_fallback_answers_when_local_cannot_locate(self):
        """本地完全查不到时，官方 API 要能兜住并给出答案。"""
        result = engine.solve(QUESTION, self._empty_store())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "官方 API 实时查询")
        self.assertTrue(result["answer"])
        for item in result["evidence"]:
            self.assertEqual(item["source"], "官方 API 实时查询")
            self.assertIn("API", item["tier_label"])

    def test_second_call_hits_cache(self):
        """缓存必须生效——官方默认 120 次/分钟，逐题查询必然超限。"""
        first = qingci.resolve_work("甲", "陈云", "谒金门", "春昼永。")
        self.assertIsNotNone(first)
        started = time.time()
        second = qingci.resolve_work("甲", "陈云", "谒金门", "春昼永。")
        elapsed = time.time() - started
        self.assertIsNotNone(second)
        self.assertLess(elapsed, 0.5, "第二次没有走缓存（%.0f ms）" % (elapsed * 1000))


if __name__ == "__main__":
    unittest.main(verbosity=2)

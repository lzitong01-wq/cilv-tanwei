"""智能体编排：引擎优先，答不上来才叫模型。

三条纪律要验住：
1. **数字永远来自引擎**——模型兜底时不产出判分字段；
2. **模型兜底必须有事实卡**——建不出事实卡就报错，**不兜底**（否则模型会编数字）；
3. **来源逐条标注**——确定性求解 / 模型兜底（未验证）/ 模型解读（未验证）分得清。

模型用 mock 注入，不依赖网络。
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import agent, store  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.errors import ApiError  # noqa: E402

C1_QUESTION = (
    "甲：清·费墨娟《临江仙·寄怀阿嫂》（词牌“临江仙”，首句“虫韵风声添寥寂，新寒初透帘前。”）。\n"
    "乙：宋·史浩《临江仙》（词牌“临江仙”，首句“忆昔来时双髻小，如今云鬓堆鸦。”）。\n"
    "两篇同用该调。请分别给出句数、最长句的句序和字数、全文平仄总数及全文仄声比例；"
    "再计算两篇全文仄声比例相差多少个百分点，并只作形式层面的有限判断。"
)

# 同一批作品锚点，但问法不在规则关键词表内 → 引擎判不出类别
OPEN_QUESTION = (
    "甲：清·费墨娟《临江仙·寄怀阿嫂》（词牌“临江仙”，首句“虫韵风声添寥寂，新寒初透帘前。”）。\n"
    "乙：宋·史浩《临江仙》（词牌“临江仙”，首句“忆昔来时双髻小，如今云鬓堆鸦。”）。\n"
    "请谈谈这两首词在格律气质上的异同。"
)


def mock_caller(payload: dict):
    """伪造一次模型调用：返回固定 JSON。"""

    def _caller(messages, model, timeout):
        return {"ok": True, "content": json.dumps(payload, ensure_ascii=False)}

    return _caller


def echo_caller(text: str):
    """模型把题面里的数字原样复读（模拟一个「只会用事实卡」的模型）。"""

    def _caller(messages, model, timeout):
        return {"ok": True, "content": text}

    return _caller


class AgentRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.settings = settings
        cls.store = store.Store(settings.snapshot_path, settings.index_path)

    def test_deterministic_question_uses_engine(self):
        """规则覆盖的题型 → 走确定性路径，字段完整。"""
        result = agent.answer(self.store, self.settings, C1_QUESTION, narrative=False)
        self.assertEqual(result["mode"], agent.MODE_DETERMINISTIC)
        self.assertTrue(result["fields"])
        self.assertTrue(result["trace"])
        self.assertEqual(result["source"], "Tier 1 官方快照")
        self.assertIsNone(result["fallback"])

    def test_open_question_falls_back_to_model(self):
        """规则覆盖不到的题型 → 模型兜底，且**不产出判分字段**。"""
        caller = mock_caller(
            {
                "draft": "两首同调，字数与平仄比例见事实卡。",
                "basis": ["facts.各篇仄声比例"],
                "verification": "未验证",
                "boundary": "不能据此判断优劣。",
            }
        )
        result = agent.answer(
            self.store, self.settings, OPEN_QUESTION, narrative=False, caller=caller
        )
        self.assertEqual(result["mode"], agent.MODE_FALLBACK)
        self.assertEqual(result["fields"], {})
        self.assertEqual(result["trace"], [])
        self.assertIsNotNone(result["fallback"])
        self.assertIn("未经确定性校验", result["fallback"]["note"])
        self.assertTrue(result["answer"])

    def test_fallback_never_produces_scoring_fields(self):
        """兜底路径的字段必须为空——数字不参与判分。"""
        caller = mock_caller(
            {
                "draft": "模型答",
                "basis": [],
                "verification": "未验证",
                "boundary": "边界",
            }
        )
        result = agent.answer(
            self.store, self.settings, OPEN_QUESTION, narrative=False, caller=caller
        )
        self.assertEqual(result["fields"], {})
        self.assertEqual(result["field_meta"], {})

    def test_model_unavailable_degrades_not_crashes(self):
        """模型挂掉 → 明确降级，不崩、不假装答上来了。"""

        def _dead(messages, model, timeout):
            return {"ok": False, "error": "连接超时"}

        result = agent.answer(
            self.store, self.settings, OPEN_QUESTION, narrative=False, caller=_dead
        )
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["answer"], "")
        self.assertEqual(result["mode"], agent.MODE_FALLBACK)

    def test_unparseable_anchor_is_rejected_before_model(self):
        """锚点都解析不出来 → 直接报错，**不交给模型**（模型也不知道怎么定位作品）。"""
        with self.assertRaises(ApiError) as ctx:
            agent.answer(self.store, self.settings, "请评价这两首词的艺术价值。", narrative=False)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_narrative_is_attached_on_deterministic_path(self):
        """确定性路径可附一段模型解读，解读是候选、标未验证。"""
        caller = mock_caller(
            {
                "draft": "两篇同为《临江仙》，句式接近，仄声比例有差。",
                "basis": ["facts.各篇仄声比例"],
                "verification": "未验证",
                "boundary": "不能据此判断优劣。",
            }
        )
        result = agent.answer(
            self.store, self.settings, C1_QUESTION, narrative=True, caller=caller
        )
        self.assertEqual(result["mode"], agent.MODE_WITH_NARRATIVE)
        narrative = result["narrative"]
        self.assertIsNotNone(narrative)
        self.assertEqual(narrative["status"], "ok")
        self.assertTrue(narrative["candidate"]["draft"])
        # 确定性字段仍在
        self.assertTrue(result["fields"])

    def test_narrative_failure_does_not_break_the_answer(self):
        """解读失败不影响主体答案。"""

        def _dead(messages, model, timeout):
            return {"ok": False, "error": "连接超时"}

        result = agent.answer(
            self.store, self.settings, C1_QUESTION, narrative=True, caller=_dead
        )
        self.assertEqual(result["mode"], agent.MODE_WITH_NARRATIVE)
        self.assertTrue(result["fields"])
        self.assertEqual(result["narrative"]["status"], "degraded")

    def test_capability_endpoint_shape(self):
        from app import create_app

        client = create_app(load_settings()).test_client()
        body = client.get("/api/agent/capabilities").get_json()["data"]
        self.assertEqual(len(body["deterministic_categories"]), 5)
        self.assertTrue(body["routing"])
        self.assertTrue(body["discipline"])

    def test_agent_endpoint_matches_core(self):
        from app import create_app

        client = create_app(load_settings()).test_client()
        response = client.post("/api/agent/answer", json={"question": C1_QUESTION, "narrative": False})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["mode"], agent.MODE_DETERMINISTIC)
        self.assertTrue(data["fields"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

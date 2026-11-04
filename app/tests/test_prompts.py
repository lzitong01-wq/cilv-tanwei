"""提示词清洁度测试（不变量 INV-PROMPT-08）。

不看常量字面量，而是抓**真实调用链上的 messages**：任何新增的副功能只要走
`sidecar.guarded`，都会被这里覆盖。断言三件事：

1. system 消息不得暴露产品身份、模型身份、竞赛/评测语境；
2. user 消息不得出现「我不能访问网络」「上下文有限」这类能力免责说明；
3. user 消息里的事实卡必须与 `build_fact_card` 的输出逐字一致——防止手工往
   提示词里塞未入卡的数字。
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import sidecar, style  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402

WORK_ID = 58633
LEFT, RIGHT = "汪东", "朱祖谋"

# 产品身份、模型身份、竞赛语境：出现在提示词里就等于把评分口径交给模型
IDENTITY_TOKENS = [
    "词律探微",
    "竞赛",
    "参赛",
    "评测集",
    "评分标准",
    "题库",
    "选手",
    "qwen",
    "Qwen",
    "step-3",
    "大模型",
    "语言模型",
]

# 能力免责：模型不需要知道自己的限制，写进去只会诱发自我设限的废话
DISCLAIMER_TOKENS = [
    "无法访问网络",
    "联网",
    "上下文长度",
    "上下文有限",
    "我不能",
    "作为一个AI",
    "作为 AI",
]


def legal_reply() -> str:
    return json.dumps(
        {
            "draft": "形式层面可对照的事实见事实卡。",
            "basis": ["facts"],
            "verification": "未验证",
            "boundary": "不能据此判定作者意图或文学价值",
        },
        ensure_ascii=False,
    )


class PromptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = load_settings()
        cls.store = Store(cls.settings.snapshot_path, cls.settings.index_path)
        cls.captured: list[list[dict]] = []

    def record(self, messages, model, timeout):
        self.captured.append(messages)
        return {"ok": True, "content": legal_reply(), "latency_ms": 5, "model": model}

    def capture_work(self) -> list[dict]:
        self.captured = []
        sidecar.generate(self.store, WORK_ID, question="这首词的形式特征是什么？", caller=self.record)
        self.assertEqual(len(self.captured), 1, "generate 应只调用一次模型")
        return self.captured[0]

    def capture_style(self) -> list[dict]:
        self.captured = []
        style.narrative(self.settings, LEFT, RIGHT, caller=self.record)
        self.assertEqual(len(self.captured), 1, "narrative 应只调用一次模型")
        return self.captured[0]

    def test_both_flows_produce_exactly_two_messages(self):
        for messages in (self.capture_work(), self.capture_style()):
            self.assertEqual([m["role"] for m in messages], ["system", "user"])

    def test_system_prompts_have_no_product_identity(self):
        for messages in (self.capture_work(), self.capture_style()):
            system = messages[0]["content"]
            for token in IDENTITY_TOKENS:
                self.assertNotIn(token, system, "系统提示暴露了 %r" % token)

    def test_prompts_have_no_capability_disclaimer(self):
        for messages in (self.capture_work(), self.capture_style()):
            for message in messages:
                for token in DISCLAIMER_TOKENS:
                    self.assertNotIn(token, message["content"], "提示词出现免责说明 %r" % token)

    def test_user_message_carries_verbatim_fact_card(self):
        messages = self.capture_work()
        card = sidecar.build_fact_card(self.store, WORK_ID)
        self.assertIn(json.dumps(card, ensure_ascii=False, indent=2), messages[1]["content"])

    def test_style_prompt_carries_verbatim_fact_card(self):
        messages = self.capture_style()
        card = style._style_fact_card(style.compare(self.settings, LEFT, RIGHT))
        self.assertIn(json.dumps(card, ensure_ascii=False, indent=2), messages[1]["content"])

    def test_system_prompt_states_output_contract(self):
        for messages in (self.capture_work(), self.capture_style()):
            system = messages[0]["content"]
            for field in ("draft", "basis", "verification", "boundary", "JSON"):
                self.assertIn(field, system)


if __name__ == "__main__":
    unittest.main(verbosity=2)

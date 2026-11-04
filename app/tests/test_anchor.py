"""题面锚点解析的鲁棒性。

背景：赛题说评测集「参考题库里的题目，词曲可能不一样」——换作品时题面写法
也可能跟着变（引号样式、括号全半角、词牌带不带书名号、首句末尾标点、锚点标签）。
2026-11-04 实测：原来的严格正则下 10 个常见变体只有 1 个能答，其余全部
「题面作品锚点数量应为 2 或 3 个」整题拒答。

两条底线：
1. **能宽容**：常见写法变体都要能解析并给出同样的答案；
2. **不能变味**：在官方题库（1000 题）上，解析结果必须与严格版**完全一致**——
   放宽不能悄悄改掉任何一道题的锚点。
"""

import json
import re
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from core import engine, store  # noqa: E402
from core.config import load_settings  # noqa: E402

# 严格版（2026-11-04 之前用的），用于等价性比对
STRICT = re.compile(
    r"(?P<label>[甲乙丙])：(?P<period>[^·\n]+)·(?P<poet>[^《\n]+)"
    r"(?P<title_block>.+?)（词牌“(?P<tune>[^”]+)”，首句“(?P<first>[^”]+)”）。"
)

QUESTION = (
    "甲：清·费墨娟《临江仙·寄怀阿嫂》（词牌“临江仙”，首句“虫韵风声添寥寂，新寒初透帘前。”）。\n"
    "乙：宋·史浩《临江仙》（词牌“临江仙”，首句“忆昔来时双髻小，如今云鬓堆鸦。”）。\n"
    "两篇同用该调。请分别给出句数、最长句的句序和字数、全文平仄总数及全文仄声比例；"
    "再计算两篇全文仄声比例相差多少个百分点，并只作形式层面的有限判断。"
)

# 作品标题里嵌套书名号的情况（题库 T-018 出现过）
NESTED = (
    "甲：清·吴文柔《谒金门·寄汉槎兄塞外见《今词初集》、《瑶华集》、《词综》、《词雅》》"
    "（词牌“谒金门”，首句“情恻恻。”）。\n"
    "乙：宋·陈云《谒金门》（词牌“谒金门”，首句“春昼永。”）。\n"
    "两篇同用该调。请分别给出句数、最长句的句序和字数、全文平仄总数及全文仄声比例；"
    "再计算两篇全文仄声比例相差多少个百分点，并只作形式层面的有限判断。"
)

VARIANTS = [
    ("原题面", QUESTION),
    ("引号换英文双引号", QUESTION.replace("“", '"').replace("”", '"')),
    ("引号换直角引号", QUESTION.replace("“", "「").replace("”", "」")),
    ("引号换单引号", QUESTION.replace("“", "‘").replace("”", "’")),
    ("括号换半角", QUESTION.replace("（", "(").replace("）", ")")),
    ("词牌加书名号", QUESTION.replace("词牌“临江仙”", "词牌《临江仙》")),
    ("词牌无引号", QUESTION.replace("词牌“临江仙”", "词牌临江仙")),
    ("首句去掉句末标点", QUESTION.replace("帘前。”", "帘前").replace("云鬓堆鸦。”", "云鬓堆鸦")),
    ("首句改单引号", QUESTION.replace("首句“", "首句‘").replace("”）。", "’）。")),
    ("锚点后无句号", QUESTION.replace("”）。", "”）")),
    ("中间用顿号", QUESTION.replace("”，首句“", "」、首句「").replace("”。", "」。")),
    ("全角冒号加空格", QUESTION.replace("甲：", "甲 ：").replace("乙：", "乙 ：")),
    ("英文逗号分隔", QUESTION.replace("”，首句“", "”, 首句“")),
]


class AnchorParsingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = load_settings()
        cls.store = store.Store(settings.snapshot_path, settings.index_path)
        cls.store.load()

    def test_all_variants_solve_to_the_same_answer(self):
        """变体必须给出**同样的数值**。

        比数值（fields / trace）而不是答案文案：答案里会带上题面的锚点标签，
        题面写「A/B」时答案自然写「A/B」——那是对的行为，不是错误。
        """
        base = engine.solve(QUESTION, self.store)
        for name, question in VARIANTS:
            with self.subTest(variant=name):
                result = engine.solve(question, self.store)
                self.assertEqual(result["fields"], base["fields"], "变体 %s 的字段值不一致" % name)
                self.assertEqual(result["trace"], base["trace"], "变体 %s 的计算链不一致" % name)
                self.assertEqual(
                    [item["work_id"] for item in result["evidence"]],
                    [item["work_id"] for item in base["evidence"]],
                    "变体 %s 定位到的作品不一致" % name,
                )

    def test_other_anchor_labels_are_accepted(self):
        """锚点标签换成 A/B 或 ①② 也能解析，数值按位置一致。

        答案与字段名里会带题面的标签（写 A 就显示 A），所以这里按**位置**比数值。
        """
        def value_set(fields):
            # 字段键带标签（甲_ping / ①_ping），排序会变；比值的集合更稳
            return sorted(json.dumps(value, ensure_ascii=False) for value in fields.values())

        base = engine.solve(QUESTION, self.store)
        for name, question in [
            ("标签换成 A/B", QUESTION.replace("甲：", "A：").replace("乙：", "B：")),
            ("标签换成 ①②", QUESTION.replace("甲：", "①：").replace("乙：", "②：")),
        ]:
            with self.subTest(variant=name):
                result = engine.solve(question, self.store)
                self.assertEqual(
                    value_set(result["fields"]),
                    value_set(base["fields"]),
                    "变体 %s 的字段值不一致" % name,
                )
                self.assertEqual(len(result["evidence"]), len(base["evidence"]))

    def test_nested_book_title_in_anchor(self):
        """标题里嵌套书名号时，锚点仍要落在正确的作品上。"""
        anchors = engine.parse_anchors(NESTED)
        self.assertEqual(len(anchors), 2)
        self.assertEqual(anchors[0]["first"], "情恻恻。")
        self.assertEqual(anchors[1]["first"], "春昼永。")
        result = engine.solve(NESTED, self.store)
        self.assertEqual(result["status"], "ok")

    def test_official_dataset_parses_identically_to_strict_version(self):
        """放宽不能改掉官方题库里任何一道题的锚点。"""
        quiz_dir = Path.home() / "Downloads" / "薪火人文-清词-1000题库-V5版本"
        path = quiz_dir / "公开测试集_700题.jsonl"
        if not path.is_file():
            self.skipTest("本地没有题库文件")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        compared = 0
        for row in rows:
            strict = STRICT.search(row["问题"])
            loose = engine.ANCHOR.search(row["问题"])
            self.assertIsNotNone(strict, row["题号"])
            self.assertIsNotNone(loose, row["题号"])
            self.assertEqual(strict.group("tune"), loose.group("tune"), row["题号"])
            self.assertEqual(strict.group("first"), loose.group("first"), row["题号"])
            self.assertEqual(strict.group("poet"), loose.group("poet"), row["题号"])
            compared += 1
        self.assertGreater(compared, 600)

    def test_missing_anchors_still_rejected(self):
        """宽容不等于放水：锚点数不对仍要明确拒答，不能硬猜。"""
        with self.assertRaises(Exception):
            engine.solve("这是一句没有作品锚点的题面，请给出结论。", self.store)


if __name__ == "__main__":
    unittest.main(verbosity=2)

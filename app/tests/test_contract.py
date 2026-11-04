"""契约一致性回归：文档 §3.2 端点表 ↔ 实现 ↔ 前端引用。

契约文档是评审会对照的东西。三方任一处漂移，评审现场都会变成「查无此端」，
所以这条检查做成用例，随回归一起跑。
"""

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
sys.path.insert(0, str(APP / "scripts"))

import check_contract  # noqa: E402


def contract_doc() -> Path:
    for candidate in (
        REPO / "前后端一致性与接口契约V1.md",
        APP / "docs" / "前后端一致性与接口契约V1.md",
    ):
        if candidate.is_file():
            return candidate
    raise unittest.SkipTest("未随包提供契约文档")


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = contract_doc()
        cls.documented = check_contract.documented_endpoints(cls.doc)
        cls.implemented, cls.raw = check_contract.implemented_endpoints()
        cls.frontend = check_contract.frontend_endpoints()

    def test_documented_matches_implementation(self):
        missing = sorted(self.implemented - self.documented)
        extra = sorted(self.documented - self.implemented)
        self.assertEqual(missing, [], "实现有、文档未列：%s" % missing)
        self.assertEqual(extra, [], "文档列了、实现没有：%s" % extra)

    def test_frontend_references_exist(self):
        broken = sorted(
            ref for ref in self.frontend if not check_contract.resolve_frontend_ref(ref, self.implemented)
        )
        self.assertEqual(broken, [], "前端引用了不存在的端点：%s" % broken)

    def test_declared_count_matches_table(self):
        declared = check_contract.documented_count(self.doc)
        self.assertIsNotNone(declared, "§3.2 标题里没写端点数量")
        self.assertEqual(
            declared,
            len(self.documented),
            "§3.2 标题写的数量与表内实际行数不一致",
        )

    def test_no_duplicate_documented_endpoints(self):
        """同一端点在表里出现两次，等于少了一个端点的说明。"""
        import re

        text = self.doc.read_text(encoding="utf-8")
        rows = re.findall(r"^\|\s*[^|]*\|\s*`((?:GET|POST|PUT|DELETE|PATCH) /api/[^`]*)`", text, re.M)
        normalized = [check_contract.normalize(row) for row in rows]
        self.assertEqual(
            len(normalized),
            len(set(normalized)),
            "端点表里有重复行：%s" % sorted({x for x in normalized if normalized.count(x) > 1}),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

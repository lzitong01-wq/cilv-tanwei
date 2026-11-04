#!/usr/bin/env python3
"""校验每条不变量都有**真实存在**的可执行用例认领。

旧版只检查用例文件在不在，用例名写错也能通过；现在用 ast 解析，要求
`path::Class::test_name` 或 `path::test_name` 在文件里确实定义。

用法：python app/scripts/check_invariants.py
退出码：0 通过；1 有 claimed 未落地；2 登记表缺失或格式错误。
"""

import ast
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = APP_ROOT / "invariants.json"


def symbols_of(path: Path) -> set[str]:
    """返回文件里可被 `::` 引用的名字：`func` 与 `Class::func`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.add("%s::%s" % (node.name, child.name))
    return found


def check_case(case: str) -> str | None:
    """返回失败原因；None 表示通过。"""
    parts = [part for part in case.split("::") if part]
    if not parts:
        return "用例为空"
    file_part, symbol = parts[0], "::".join(parts[1:])
    target = APP_ROOT / file_part
    if not target.is_file():
        return "文件不存在"
    if not symbol:
        return "未写到具体用例"
    try:
        names = symbols_of(target)
    except SyntaxError as exc:
        return "用例文件无法解析：%s" % exc
    if symbol not in names:
        return "用例名不存在（文件里没有 %s）" % symbol
    return None


def main() -> int:
    if not REGISTRY.is_file():
        print("登记表不存在：%s" % REGISTRY, file=sys.stderr)
        return 2
    try:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print("登记表不是合法 JSON：%s" % exc, file=sys.stderr)
        return 2

    items = registry.get("invariants") or []
    if not items:
        print("登记表为空", file=sys.stderr)
        return 2

    claimed = [item for item in items if item.get("status") == "claimed"]
    pending = [item for item in items if item.get("status") != "claimed"]

    broken = []
    for item in claimed:
        reason = check_case(item.get("case") or "")
        if reason:
            broken.append((item.get("id"), item.get("case"), reason))

    # pending 项若写了 case，不允许它指向不存在的用例——避免用假用例占位
    fake_pending = []
    for item in pending:
        case = item.get("case") or ""
        if case and check_case(case):
            fake_pending.append((item.get("id"), case))

    print("不变量 %d 条：claimed %d、pending %d" % (len(items), len(claimed), len(pending)))
    if broken:
        for inv_id, case, reason in broken:
            print("  失效认领：%s -> %s（%s）" % (inv_id, case, reason), file=sys.stderr)
    if fake_pending:
        for inv_id, case in fake_pending:
            print("  pending 指向不存在的用例：%s -> %s" % (inv_id, case), file=sys.stderr)
    if pending:
        print("  pending（不计入通过率）：")
        for item in pending:
            note = item.get("note") or "—"
            print("    %s %s" % (item.get("id"), note))
    if broken or fake_pending:
        return 1
    print("结果：通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

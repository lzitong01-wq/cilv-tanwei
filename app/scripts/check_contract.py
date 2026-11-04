#!/usr/bin/env python3
"""契约一致性检查：契约文档、真实实现、前端引用，三方逐条比对。

为什么要有这个：契约文档是评审会对照的东西，文档写了但实现没有（或反之），
就是「查无此端」。这个脚本把三类差异都列出来：

1. 文档 §3.2 列了、实现没有          —— 文档超前或实现漏了
2. 实现有、文档 §3.2 没列            —— 实现扩展后文档没跟上
3. 前端代码引用了、实现没有          —— 点了就是 404

「路径参数写法不同但语义相同」（如 `{id}` 与 `<int:work_id>`）由一张**等价表**
处理；等价表的键是**规范化后**的写法，所以两边都能对上。

用法：python app/scripts/check_contract.py [--doc 前后端一致性与接口契约V1.md]
退出码：0 三方一致；1 有差异；2 找不到文档
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent

# 语义等价、写法不同的一组端点：键=文档写法（已规范化），值=实现写法（已规范化）
EQUIVALENT = {
    "GET /api/graph/*/neighbors": "GET /api/graph/neighbors",
    "GET /api/revision/export/*": "POST /api/revision/export",
    "POST /api/revision/export": "POST /api/revision/export",
}

# 前端里用模板串拼路径时，前缀本身就是一个不完整路径，这里登记为前缀匹配
FRONTEND_PREFIX_ALIAS = {
    "GET /api/eval/": "GET /api/eval/",
    "GET /api/qa/": "GET /api/qa/",
}


def normalize(path: str) -> str:
    """把路径参数段统一成 *，方法与路径分开。"""
    path = path.strip().strip("`")
    method = ""
    for candidate in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        if path.upper().startswith(candidate + " "):
            method = candidate
            path = path[len(candidate) :].strip()
            break
    path = path.split("?")[0]
    trailing = path.endswith("/")
    parts = []
    for part in path.split("/"):
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            parts.append("*")
        elif part.startswith("{") and part.endswith("}"):
            parts.append("*")
        elif "${" in part:
            parts.append("*")
        else:
            parts.append(part)
    if trailing:
        # 保留末尾斜杠：前端 `"/api/eval/" + jobId` 是前缀拼接，不是完整端点
        parts.append("")
    return "%s /%s" % (method, "/".join(parts))


def documented_endpoints(doc_path: Path) -> set[str]:
    doc = doc_path.read_text(encoding="utf-8")
    found = set()
    for raw in re.findall(r"`((?:GET|POST|PUT|DELETE|PATCH) /api/[^`]*)`", doc):
        normalized = normalize(raw)
        found.add(EQUIVALENT.get(normalized, normalized))
    return found


def documented_count(doc_path: Path) -> int | None:
    """读 §3.2 标题里声明的端点数量（写作「端点表（N 个）」）。"""
    doc = doc_path.read_text(encoding="utf-8")
    match = re.search(r"###\s*3\.2[^\n]*?（(\d+)\s*个）", doc)
    return int(match.group(1)) if match else None


def implemented_endpoints() -> tuple[set[str], dict[str, str]]:
    """返回 (规范化后的端点集合, 规范化 → 原始写法的映射)。

    规范化把路径参数段统一成 *，所以文档写 `{id}`、实现写 `<int:work_id>`
    都能对上；报告时用原始写法，便于直接照抄进文档。

    导入用绝对路径 + importlib 按文件加载，不靠 sys.path 里的模块名解析：
    目标机上 `app/` 既是外层目录名（内含 backend/、data/…）又是应用模块名
    （backend/app.py），依赖 `import app` 的名字解析会命中错误的那个，
    报 `cannot import name 'corpus' from 'core'`。
    """
    backend_dir = str(APP / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    # 清掉可能被别处塞进来的同名模块，避免拿到错误的 'app' / 'core'
    for name in ("app", "core"):
        module = sys.modules.get(name)
        if module is not None and not hasattr(module, "__file__"):
            sys.modules.pop(name, None)

    app_module = _load_module_by_path("app", APP / "backend" / "app.py")
    config_module = _load_module_by_path("core.config", APP / "backend" / "core" / "config.py")
    core_package = _load_module_by_path("core", APP / "backend" / "core" / "__init__.py")

    # 让 core.config 能找到 core 包
    sys.modules.setdefault("core", core_package)
    sys.modules["core.config"] = config_module

    create_app = app_module.create_app
    load_settings = config_module.load_settings

    application = create_app(load_settings())
    found: set[str] = set()
    raw: dict[str, str] = {}
    for rule in application.url_map.iter_rules():
        if rule.rule.startswith("/static") or rule.rule in ("/", "/<path:asset>"):
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            key = normalize("%s %s" % (method, rule.rule))
            found.add(key)
            raw.setdefault(key, "%s %s" % (method, rule.rule))
    return found, raw


def _load_module_by_path(name: str, path: Path):
    """按文件路径加载模块，模块名固定为 name。

    这样不依赖当前工作目录，也不依赖 sys.path 的名字解析顺序。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载模块 %s（%s）" % (name, path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def frontend_endpoints() -> set[str]:
    """扫前端源码里出现的所有 /api/ 调用点（含模板串拼接的前缀）。"""
    found = set()
    pattern = re.compile(r"""["'`]((?:GET|POST|PUT|DELETE|PATCH) )?(/api/[^"'`\s]*)""")
    for path in sorted((APP / "frontend" / "src").rglob("*.ts*")):
        text = path.read_text(encoding="utf-8")
        for method, raw in pattern.findall(text):
            method = method or "GET"
            normalized = normalize("%s %s" % (method, raw))
            found.add(normalized)
    return found


def resolve_frontend_ref(ref: str, implemented: set[str]) -> bool:
    """前端引用是否能在实现里找到。

    前端的 HTTP 方法写在 fetch 的 options 里，不在路径字符串里，
    所以这里按**路径**判定存在性：路径对上即认为端点存在（方法另由冒烟脚本覆盖）。
    """
    if ref in implemented:
        return True
    alias = EQUIVALENT.get(ref)
    if alias and alias in implemented:
        return True
    path = ref.split(" ", 1)[1] if " " in ref else ref
    # 模板串拼接：`/api/eval/` + jobId → 前缀匹配
    if path.endswith("/"):
        return any(item.split(" ", 1)[1].startswith(path) for item in implemented)
    return any(item.split(" ", 1)[1] == path for item in implemented)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", default=str(REPO / "前后端一致性与接口契约V1.md"))
    args = parser.parse_args()

    doc_path = Path(args.doc)
    if not doc_path.is_file():
        print("找不到契约文档：%s" % doc_path, file=sys.stderr)
        return 2

    documented = documented_endpoints(doc_path)
    implemented, raw = implemented_endpoints()
    frontend = frontend_endpoints()

    missing_in_doc = sorted(implemented - documented)
    extra_in_doc = sorted(documented - implemented)
    frontend_missing = sorted(ref for ref in frontend if not resolve_frontend_ref(ref, implemented))

    declared = documented_count(doc_path)
    print("契约文档：%s" % doc_path.name)
    print("实现端点 %d，文档列出 %d，前端引用 %d" % (len(implemented), len(documented), len(frontend)))
    if declared is not None and declared != len(documented):
        print("\n[!] 文档 §3.2 标题声明 %d 个端点，表内实际 %d 行，请改标题或补表。" % (declared, len(documented)))

    if missing_in_doc:
        print("\n实现有、文档未列（%d）：" % len(missing_in_doc))
        for item in missing_in_doc:
            print("   ", raw.get(item, item))
    if extra_in_doc:
        print("\n文档列了、实现没有（%d）：" % len(extra_in_doc))
        for item in extra_in_doc:
            print("   ", item)
    if frontend_missing:
        print("\n前端引用、实现没有（%d）：" % len(frontend_missing))
        for item in frontend_missing:
            print("   ", item)
    if not missing_in_doc and not extra_in_doc and not frontend_missing:
        print("\n完全一致。")
        return 0
    print("\n请同步契约文档 §3.2 端点表（实现新增的注明是规划后扩展）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())

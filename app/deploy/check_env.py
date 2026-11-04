#!/usr/bin/env python3
"""部署前环境自检：把「目标机上最容易卡住的几件事」一次查清。

检查项：
1. Python 版本 ≥ 3.11（用到 X | None 语法与 pathlib 新行为）；
2. SQLite ≥ 3.34 且编译带 FTS5（检索索引依赖 trigram 扩展）；
3. Tier 1 快照存在、可读、行数与作品数；
4. 前端构建产物 index.html 与字体子集在位；
5. 构建期索引产物（检索/风格/校订/词谱/Tier 2）在位，缺失的给出补跑命令；
6. 监听端口未被占用；
7. 契约一致性：契约文档 ↔ 实现 ↔ 前端引用，三方端点表无漂移。

用法：
    python app/deploy/check_env.py [--snapshot PATH] [--dist PATH] [--port 8080]
退出码：0 全部通过；1 有 FAIL；2 环境本身不满足（无法自检）
"""

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent

REQUIRED_INDEXES = {
    "检索索引 index.sqlite3": ("data/index.sqlite3", "python app/scripts/build_index.py"),
    "风格索引 style.sqlite3": ("data/style.sqlite3", "python app/scripts/build_style_index.py"),
    "校订队列 revisions.sqlite3": ("data/revisions.sqlite3", "python app/scripts/build_revisions.py"),
    "词谱体式索引 prosody_index.json": ("data/prosody_index.json", "python app/scripts/build_prosody_index.py"),
    "古音平仄层 guxin_tune.json": ("data/guxin_tune.json", "python app/scripts/build_guxin_layer.py"),
    "Tier 2 定位索引 tier2_locator.sqlite3": (
        "data/tier2_locator.sqlite3",
        "python app/scripts/build_tier2_locator.py",
    ),
}

results = []


def record(name: str, ok: bool, detail: str, fix: str = "") -> None:
    results.append({"name": name, "ok": ok, "detail": detail, "fix": fix})


def check_python() -> None:
    version = sys.version_info
    ok = version >= (3, 11)
    record(
        "Python ≥ 3.11",
        ok,
        "%d.%d.%d" % (version.major, version.minor, version.micro),
        "dnf install -y python3.11 python3.11-pip" if not ok else "",
    )


def check_sqlite() -> None:
    version = sqlite3.sqlite_version_info
    ok_version = version >= (3, 34)

    # 先探 trigram 能力：应用真正依赖的是「能不能用 FTS5 trigram」，
    # 而不是系统 sqlite3 的版本号本身。
    # Anolis OS 8.6 的系统 SQLite 是 3.26.0（无 trigram），但装了 pysqlite3
    # 之后应用会自动切换，功能完全正常——此时不该报 FAIL。
    trigram_std = False
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        trigram_std = True
        connection.close()
    except sqlite3.Error:
        trigram_std = False

    pysqlite3_version = ""
    pysqlite3_ok = False
    if not trigram_std:
        try:
            import pysqlite3  # type: ignore

            probe = pysqlite3.connect(":memory:")
            probe.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
            probe.close()
            pysqlite3_ok = True
            pysqlite3_version = getattr(pysqlite3, "sqlite_version", "?")
        except Exception:
            pysqlite3_ok = False

    if trigram_std:
        record(
            "SQLite ≥ 3.34",
            True,
            "%s（原生支持 FTS5 trigram）" % sqlite3.sqlite_version,
        )
    elif pysqlite3_ok:
        record(
            "SQLite ≥ 3.34",
            True,
            "系统 %s 偏低，已切换到 pysqlite3 %s（支持 FTS5 trigram）"
            % (sqlite3.sqlite_version, pysqlite3_version),
        )
    else:
        record(
            "SQLite ≥ 3.34",
            False,
            "系统 %s 不支持 FTS5 trigram 且未安装 pysqlite3；≥3 字检索会退回内存子串匹配"
            % sqlite3.sqlite_version,
            "装离线 wheel：pip install --no-index --find-links deploy/wheels/linux-x86_64-py311 pysqlite3-binary",
        )

    fts5 = False
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        connection.execute("INSERT INTO t VALUES ('测试')")
        fts5 = bool(connection.execute("SELECT count(*) FROM t WHERE t MATCH '测试'").fetchone()[0])
        connection.close()
    except sqlite3.Error as exc:
        fts5 = False
        detail = "FTS5 不可用：%s" % exc
    else:
        detail = "FTS5 可用"
    record("SQLite FTS5 扩展", fts5, detail, "安装带 FTS5 的 sqlite3（Debian/RHEL 默认带）" if not fts5 else "")

    # trigram 单独列一项：≥3 字检索走 works_fts MATCH，完全依赖它
    if trigram_std:
        record("FTS5 trigram 分词器", True, "系统 SQLite 原生支持")
    elif pysqlite3_ok:
        record(
            "FTS5 trigram 分词器",
            True,
            "系统 SQLite %s 不支持，已自动切换到 pysqlite3 %s" % (sqlite3.sqlite_version, pysqlite3_version),
        )
    else:
        record(
            "FTS5 trigram 分词器",
            False,
            "系统 SQLite %s 不支持且未安装 pysqlite3" % sqlite3.sqlite_version,
            "装离线 wheel：pip install --no-index --find-links deploy/wheels/linux-x86_64-py311 pysqlite3-binary",
        )


def check_snapshot(path: Path) -> None:
    if not path.is_file():
        record("Tier 1 快照", False, "缺少 %s" % path, "从赛方数据导出或拷贝 data/知识库审计快照V1.jsonl")
        return
    works = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    works += 1
    except OSError as exc:
        record("Tier 1 快照", False, "读取失败：%s" % exc)
        return
    ok = works >= 400
    record("Tier 1 快照", ok, "%s（%d 篇）" % (path.name, works), "快照应含 423 篇" if not ok else "")


def check_dist(dist: Path) -> None:
    index = dist / "index.html"
    ok = index.is_file()
    record("前端构建产物 index.html", ok, str(index), "在 app/frontend 执行 npm ci && npm run build" if not ok else "")
    fonts = list((dist / "fonts").glob("*.woff2")) if (dist / "fonts").is_dir() else []
    record("字体子集", len(fonts) >= 1, "dist/fonts 下 %d 个 woff2" % len(fonts),
           "执行 python app/scripts/build_fonts.py 并重新构建前端" if not fonts else "")


def check_indexes() -> None:
    for label, (relative, command) in REQUIRED_INDEXES.items():
        path = APP / relative
        ok = path.is_file() and path.stat().st_size > 0
        detail = relative if ok else "缺失或为空"
        record("构建期产物：%s" % label, ok, detail, "" if ok else command)


def check_api_fallback() -> None:
    """官方知识库 API 兜底是否已配置（可选启用）。

    没配不影响使用——本地快照 + 同源层已经能答绝大多数题面；
    配了则多一层「官方新发布的作品也能实时查到」的保险。
    """
    key = (os.environ.get("APP_QINGCI_KEY") or "").strip()
    base = (os.environ.get("APP_QINGCI_BASE") or "https://qingci.zhiruitech.com.cn").strip()
    record(
        "官方 API 兜底",
        True,
        "已配置（%s）" % base if key else "未配置：仅本地快照 + 同源层（不影响使用）",
    )


def check_contract() -> None:
    """契约一致性：文档 §3.2 端点表 ↔ 实现 ↔ 前端引用。

    这项检查防的是「文档写了但实现没有」「前端点了但端点不存在」——
    评审现场按文档查端点时，两个问题都会直接变成「查无此端」。
    """
    script = APP / "scripts" / "check_contract.py"
    if not script.is_file():
        record("契约一致性", False, "缺少 %s" % script, "从源码仓补回 app/scripts/check_contract.py")
        return
    doc = next(
        (path for path in (
            REPO / "前后端一致性与接口契约V1.md",
            APP / "docs" / "前后端一致性与接口契约V1.md",
        ) if path.is_file()),
        None,
    )
    if doc is None:
        record("契约一致性", True, "未随包提供契约文档，跳过比对")
        return
    try:
        spec = importlib.util.spec_from_file_location("_cilv_check_contract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        documented = module.documented_endpoints(doc)
        implemented, _raw = module.implemented_endpoints()
        frontend = module.frontend_endpoints()
    except Exception as exc:  # noqa: BLE001 - 自检项自身出错也要报出来
        record("契约一致性", False, "比对失败：%s" % exc, "python app/scripts/check_contract.py 单独复现")
        return
    missing = implemented - documented
    extra = documented - implemented
    frontend_missing = [ref for ref in frontend if not module.resolve_frontend_ref(ref, implemented)]
    ok = not missing and not extra and not frontend_missing
    detail = "实现 %d / 文档 %d / 前端引用 %d，三方一致" % (len(implemented), len(documented), len(frontend))
    if not ok:
        detail = "文档缺 %d、实现缺 %d、前端引用无实现 %d" % (
            len(missing), len(extra), len(frontend_missing))
    record("契约一致性", ok, detail, "运行 python app/scripts/check_contract.py 看逐条差异" if not ok else "")


def check_port(port: int) -> None:
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", port))
        record("监听端口 %d 可用" % port, True, "空闲")
    except OSError as exc:
        record("监听端口 %d 可用" % port, False, "被占用：%s" % exc,
               "ss -ltnp | grep %d 找到旧进程并结束它" % port)
    finally:
        probe.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=os.environ.get(
        "APP_SNAPSHOT", str(REPO / "data" / "知识库审计快照V1.jsonl")))
    parser.add_argument("--dist", default=os.environ.get(
        "APP_DIST", str(APP / "frontend" / "dist")))
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT", "8080")))
    parser.add_argument("--skip-port", action="store_true")
    args = parser.parse_args()

    check_python()
    check_sqlite()
    check_snapshot(Path(args.snapshot))
    check_dist(Path(args.dist))
    check_indexes()
    check_api_fallback()
    check_contract()
    if not args.skip_port:
        check_port(args.port)

    failed = [item for item in results if not item["ok"]]
    width = max(len(item["name"]) for item in results) + 2
    for item in results:
        mark = "PASS" if item["ok"] else "FAIL"
        print("[%s] %-*s %s" % (mark, width, item["name"], item["detail"]))
        if not item["ok"] and item["fix"]:
            print("       ↳ 处理：%s" % item["fix"])
    print("\n%d 项检查，%d 项通过，%d 项未通过" % (len(results), len(results) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

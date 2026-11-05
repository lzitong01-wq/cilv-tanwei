#!/usr/bin/env python3
"""交付包打包：一条命令复现交付物结构，避免每次手工挑文件。

包内容：
    app/            后端、前端源码与构建产物、构建期索引、脚本、测试、交付文档
    data/           Tier 1 官方快照（运行时必需）
    tools/          规划期探针与分析脚本
    docs/           报告与总览
    *.md            顶层规划与规范文档

排除（有意为之）：
    node_modules / .venv / __pycache__ / *.pyc / .pytest_cache
    词谱与同源语料源文件（构建期输入，不随交付分发）

用法：
    python app/scripts/build_package.py [--out 词律探微-交付包-YYYYMMDD.tar.gz]
退出码：0 打包成功并打印清单；1 打包失败。
"""

import argparse
import fnmatch
import sys
import tarfile
from datetime import date
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent

EXCLUDE_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".git",
    ".idea",
    ".vscode",
    # 字体源文件（构建期输入，12 MB/字重）；子集产物在 public/fonts 与 dist/fonts
    ".fontsrc",
    # playwright-cli 跑逐页/交互检查时留下的页面快照与控制台日志
    ".playwright-cli",
}
EXCLUDE_FILES = ["*.pyc", "*.pyo", "*.log", ".DS_Store"]
EXCLUDE_PATHS = [
    # 构建期输入：词谱与同源语料源文件（派生事实保留，源文件不分发）
    "app/data/_sources",
    "app/data/sources",
    "app/frontend/public/fonts/*.ttf",
    # 运行时缓存：官方 API 兜底查到的作品，属本机状态，不随包分发
    "app/data/api_cache.sqlite3",
]

# 顶层需要进包的文件与目录
INCLUDE_TOP_FILES = ["*.md"]
INCLUDE_TOP_DIRS = ["app", "data", "tools", "docs"]


def excluded(relative: str) -> bool:
    parts = Path(relative).parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    name = parts[-1]
    if any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_FILES):
        return True
    for pattern in EXCLUDE_PATHS:
        if fnmatch.fnmatch(relative, pattern):
            return True
    return False


def collect() -> list[tuple[Path, str]]:
    """返回 [(磁盘路径, 包内相对路径)]，已按排除规则过滤。"""
    items: list[tuple[Path, str]] = []
    for pattern in INCLUDE_TOP_FILES:
        for path in sorted(REPO.glob(pattern)):
            if path.is_file():
                items.append((path, path.name))
    for dirname in INCLUDE_TOP_DIRS:
        base = REPO / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(REPO).as_posix()
            if excluded(relative):
                continue
            items.append((path, relative))
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(REPO / ("词律探微-交付包-%s.tar.gz" % date.today().strftime("%Y%m%d"))))
    args = parser.parse_args()
    out = Path(args.out)

    items = collect()
    if not items:
        print("没有可打包的文件", file=sys.stderr)
        return 1

    with tarfile.open(out, "w:gz", compresslevel=9) as archive:
        for path, arcname in items:
            archive.add(path, arcname=arcname)

    size_mb = out.stat().st_size / 1024 / 1024
    print("打包完成：%s" % out.name)
    print("  文件数 %d，体积 %.1f MB" % (len(items), size_mb))
    print("  排除：%s" % "、".join(sorted(EXCLUDE_DIRS)))
    # 清单落盘，便于复核包内容
    manifest = REPO / "app" / "deploy" / "包内容清单.txt"
    manifest.write_text(
        "\n".join(arc for _p, arc in items) + "\n",
        encoding="utf-8",
    )
    print("  清单：%s" % manifest.relative_to(REPO))

    # 打包后校验：源码目录里该进包的文件是否都在包里。
    # 2026-11-04 实测踩过：包里少了 core/corpus.py，服务器上应用直接起不来
    # （`from core import corpus` 报 ImportError），而打包脚本当时没有任何告警。
    missing = verify_source_complete(items)
    if missing:
        print("打包校验未通过：以下源码文件未进包", file=sys.stderr)
        for path in missing:
            print("  ✗ %s" % path, file=sys.stderr)
        print("交付包不可用，已中止。", file=sys.stderr)
        return 1
    print("  校验：源码 .py 文件全部在包内")
    return 0


def verify_source_complete(items: list[tuple[Path, str]]) -> list[str]:
    """返回「在源码里但没进包」的 .py 文件列表。

    只查 Python 源码——它们是应用能否启动的决定因素，
    漏一个就会在目标机上变成 ImportError，而不是在这里。
    """
    archived = {arc for _p, arc in items}
    missing: list[str] = []
    for source_dir in (REPO / "app" / "backend", REPO / "app" / "scripts"):
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.rglob("*.py")):
            relative = path.relative_to(REPO).as_posix()
            if excluded(relative):
                continue
            if relative not in archived:
                missing.append(relative)
    return missing


if __name__ == "__main__":
    sys.exit(main())

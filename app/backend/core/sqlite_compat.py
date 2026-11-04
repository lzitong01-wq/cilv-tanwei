"""SQLite 兼容层：解决目标机系统 SQLite 过旧、不支持 FTS5 trigram 分词器的问题。

背景（2026-11-04 龙蜥服务器实测）：

- Anolis OS 8.6 的系统 SQLite 是 **3.26.0**，不支持 `tokenize='trigram'`
  （该分词器自 SQLite 3.34 起提供）。
- 仓库里 `index.sqlite3` 是**构建期产物**，用 3.34+ 建的 FTS5(trigram) 虚表；
  在 3.26.0 上打开会报 `no such tokenizer: trigram`。
- 检索有两条路径（见 `core/store.py` 的 `search`）：
  - 1–2 字走 `grams` 一元/二元倒排表 → **纯 SQL 表，3.26 可读，不受影响**；
  - ≥3 字走 `works_fts` MATCH → **依赖 trigram，3.26 不可用**。
- Anolis 源里没有更新版的 `sqlite-libs`，装不上新版。

因此这里的做法是：**只在需要 trigram 的那条路径上**改用 `pysqlite3`
（PyPI 上的预编译轮子，自带新版 SQLite），其余路径仍用标准库 `sqlite3`。

`pysqlite3` 是可选依赖：装了就用，没装就回退到标准库，
回退后 ≥3 字检索会走 `_search_memory` 的内存子串兜底（功能可用，只是慢）。
"""

from __future__ import annotations

import sqlite3

_STANDARD_SQLITE_VERSION = sqlite3.sqlite_version_info


def _probe_trigram(module) -> bool:
    """检查给定 sqlite3 模块是否支持 FTS5 trigram 分词器。"""
    try:
        connection = module.connect(":memory:")
    except Exception:
        return False
    try:
        connection.execute("CREATE VIRTUAL TABLE _t USING fts5(x, tokenize='trigram')")
        return True
    except Exception:
        return False
    finally:
        connection.close()


def _load_pysqlite3():
    """尝试加载 pysqlite3；不可用时返回 None。"""
    try:
        import pysqlite3  # type: ignore

        return pysqlite3
    except Exception:
        return None


_PYSQLITE3 = _load_pysqlite3()

# 只有当「标准库不支持 trigram」且「pysqlite3 支持 trigram」时才启用。
# 顺序不能反：本机（Windows / 新版 SQLite）应当继续用标准库，
# 避免无谓地引入一个额外的二进制依赖。
_TRIGRAM_CAPABLE = None
if _PYSQLITE3 is not None and not _probe_trigram(sqlite3):
    _TRIGRAM_CAPABLE = _probe_trigram(_PYSQLITE3)


def trigram_capable() -> bool:
    """当前解释器是否具备 FTS5 trigram 能力。"""
    if _TRIGRAM_CAPABLE is not None:
        return _TRIGRAM_CAPABLE
    return _probe_trigram(sqlite3)


def sqlite_version() -> str:
    """实际用于 trigram 检索的 SQLite 版本。"""
    if _TRIGRAM_CAPABLE:
        return getattr(_PYSQLITE3, "sqlite_version", "?")
    return sqlite3.sqlite_version


def connect(uri: str, *, read_only: bool = False):
    """打开数据库连接。

    - 标准库支持 trigram 时，直接用标准库（与原行为完全一致）。
    - 标准库不支持但 `pysqlite3` 可用时，用 `pysqlite3` 打开，**并把 uri 里的
      `mode=ro` 参数去掉**——`pysqlite3` 的 URI 处理与标准库略有差异，
      只读模式由我们自己保证（连接后不执行写操作）。
    """
    if not _TRIGRAM_CAPABLE:
        connection = sqlite3.connect(uri, uri=uri.startswith("file:"))
        connection.row_factory = sqlite3.Row
        return connection

    target_uri = uri
    if read_only:
        target_uri = uri.replace("?mode=ro", "").replace("&mode=ro", "")

    connection = _PYSQLITE3.connect(target_uri, uri=target_uri.startswith("file:"))
    connection.row_factory = _PYSQLITE3.Row
    return connection


def backend_name() -> str:
    """当前实际使用的 SQLite 后端标识，用于 /api/health 如实展示。"""
    if _TRIGRAM_CAPABLE:
        return f"pysqlite3 {getattr(_PYSQLITE3, 'sqlite_version', '?')}"
    return f"stdlib {sqlite3.sqlite_version}"


# 行工厂：跟随实际使用的后端，避免 `connection.row_factory = sqlite3.Row`
# 与 pysqlite3 的 Row 不兼容。
Row = _PYSQLITE3.Row if _TRIGRAM_CAPABLE else sqlite3.Row


def explain() -> str:
    """给运维看的一句话说明。"""
    if _TRIGRAM_CAPABLE:
        return (
            f"系统 SQLite {sqlite3.sqlite_version} 不支持 FTS5 trigram，"
            f"已自动切换到 pysqlite3 {getattr(_PYSQLITE3, 'sqlite_version', '?')}。"
        )
    if not trigram_capable():
        return (
            f"系统 SQLite {sqlite3.sqlite_version} 不支持 FTS5 trigram，"
            "且未安装 pysqlite3；≥3 字检索将回退到内存子串匹配（可用但较慢）。"
        )
    return f"系统 SQLite {sqlite3.sqlite_version} 原生支持 FTS5 trigram。"

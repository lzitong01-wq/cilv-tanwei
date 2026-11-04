"""校订队列：查询、人工复核、幂等导出。

闭环止于导出（ADR-0007）：官方 API 只读（405），写回由官方流程完成。
幂等键取本次导出条目的内容哈希：同样内容重复导出得到同一 key，不会产生重复回写。
"""

import csv
import hashlib
import io
import json
import sqlite3
import threading
from pathlib import Path

from . import polyphone
from .errors import ApiError

VALID_STATUS = {"pending", "accepted", "rejected"}
VALID_DECISION = {"accept", "reject"}

_RULES_CACHE: dict = {}
_RULES_LOCK = threading.Lock()


def _rules(settings) -> tuple[list[dict], dict]:
    """加载多音字修正表（构建期产物）；缺失时返回空表，队列照常工作。"""
    path = Path(settings.polyphone_rules_path)
    with _RULES_LOCK:
        if "rules" in _RULES_CACHE:
            return _RULES_CACHE["rules"], _RULES_CACHE["index"]
    rules: list[dict] = []
    index: dict = {}
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        rules = list(payload.get("rules") or []) + list(payload.get("char_rules") or [])
        for rule in rules:
            index.setdefault(rule["char"], []).append(rule)
    with _RULES_LOCK:
        _RULES_CACHE.update({"rules": rules, "index": index})
    return rules, index


def _suggestion(settings, row) -> dict | None:
    """L1 建议值：按修正表给出「把哪个字读成什么」的建议。

    只在**已知官方计数**的前提下给建议（目标明确），否则宁可不给。
    依据来自差异归纳出的上下文规则，不是手写读音。
    """
    rules, index = _rules(settings)
    if not rules:
        return None
    result = polyphone.apply_rules(row["line_text"], rules, index, target_ping=row["api_ping"])
    hits = result.get("hits") or []
    if not hits:
        return None
    hit = hits[0]
    items = result["items"]
    context = items[hit["index"]]["char"] if hit["index"] < len(items) else ""
    return {
        "char": hit["char"],
        "reading": hit["reading"],
        "exact_context": hit.get("exact", False),
        "basis": "修正表（由校订差异归纳）" if hit.get("exact") else "修正表字级兜底（跨语料泛化弱）",
        "context": context,
    }


def _connect(settings) -> sqlite3.Connection:
    path = Path(settings.revision_index_path)
    if not path.is_file():
        raise ApiError(
            "NOT_FOUND",
            "校订队列未生成",
            {"expected_path": str(path), "hint": "运行 python app/scripts/build_revisions.py"},
        )
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def stats(settings) -> dict:
    connection = _connect(settings)
    try:
        rows = dict(connection.execute("SELECT status, count(*) FROM revisions GROUP BY status"))
        kinds = dict(connection.execute("SELECT kind, count(*) FROM revisions GROUP BY kind"))
        exported = connection.execute(
            "SELECT count(*) FROM revisions WHERE exported_at IS NOT NULL"
        ).fetchone()[0]
        total = connection.execute("SELECT count(*) FROM revisions").fetchone()[0]
    finally:
        connection.close()
    return {
        "total": total,
        "by_status": {status: rows.get(status, 0) for status in ("pending", "accepted", "rejected")},
        "by_kind": kinds,
        "exported": exported,
        "closure_level": "L3 幂等导出（写回由官方流程完成）",
    }


def list_items(settings, status: str = "pending", kind: str = "", page: int = 1, page_size: int = 20) -> dict:
    if status and status not in VALID_STATUS:
        raise ApiError("INVALID_INPUT", "status 取值 pending / accepted / rejected", {"status": status})
    where = []
    params: list = []
    if status:
        where.append("status = ?")
        params.append(status)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    connection = _connect(settings)
    try:
        total = connection.execute("SELECT count(*) FROM revisions" + clause, params).fetchone()[0]
        rows = connection.execute(
            # 多音字可解释的排前面（有建议值可看），暂无法解释的后置
            "SELECT * FROM revisions" + clause
            + " ORDER BY (kind = 'polyphone') DESC, work_id, line_no LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
    finally:
        connection.close()
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "work_id": row["work_id"],
                "title": row["title"],
                "poet": row["poet"],
                "period": row["period"],
                "line_no": row["line_no"],
                "line_text": row["line_text"],
                "local": {"ping": row["local_ping"], "ze": row["local_ze"]},
                "api": {"ping": row["api_ping"], "ze": row["api_ze"]},
                "kind": row["kind"],
                "reason": row["reason"],
                "candidates": __import__("json").loads(row["candidates"] or "[]"),
                "status": row["status"],
                "note": row["note"],
                "exported_at": row["exported_at"],
                "suggestion": _suggestion(settings, row) if row["kind"] == "polyphone" else None,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def decide(settings, revision_id: str, decision: str, note: str = "") -> dict:
    if decision not in VALID_DECISION:
        raise ApiError("INVALID_INPUT", "decision 取值 accept 或 reject")
    if len(note) > 200:
        raise ApiError("INVALID_INPUT", "备注过长（≤200 字）")
    connection = _connect(settings)
    try:
        row = connection.execute("SELECT status FROM revisions WHERE id = ?", (revision_id,)).fetchone()
        if row is None:
            raise ApiError("NOT_FOUND", "校订条目不存在", {"id": revision_id})
        if row["status"] != "pending":
            raise ApiError(
                "INVALID_INPUT",
                "该条目已处理，不能重复决策",
                {"id": revision_id, "status": row["status"]},
            )
        status = "accepted" if decision == "accept" else "rejected"
        connection.execute(
            "UPDATE revisions SET status = ?, note = ?, decided_at = datetime('now') WHERE id = ?",
            (status, note, revision_id),
        )
        connection.commit()
    finally:
        connection.close()
    return {"id": revision_id, "status": status}


def _ensure_export_table(connection: sqlite3.Connection) -> None:
    """导出记录表：懒创建。

    校订库是构建期产物，运行时只做「已接受条目 → 导出」这一个写动作，
    所以导出记录表在建库脚本之外懒创建，避免改动构建期 schema 的版本口径。
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS revision_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_key TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            row_count INTEGER NOT NULL,
            accepted_total INTEGER NOT NULL,
            pending_total INTEGER NOT NULL,
            filename TEXT NOT NULL
        )
        """
    )


def export_history(settings, limit: int = 50) -> dict:
    """导出记录：每次导出的时间、幂等键、行数，供「导出记录」页展示。

    表不存在时返回空列表而不是报错——没导过时页面显示空状态即可。
    """
    connection = _connect(settings)
    try:
        exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='revision_exports'"
        ).fetchone()
        if exists is None:
            return {"items": [], "total": 0, "closure_level": "L3 幂等导出（写回由官方流程完成）"}
        total = connection.execute("SELECT count(*) FROM revision_exports").fetchone()[0]
        rows = connection.execute(
            "SELECT id, export_key, created_at, row_count, accepted_total, pending_total, filename "
            "FROM revision_exports ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    finally:
        connection.close()
    items = [
        {
            "id": row["id"],
            "export_key": row["export_key"],
            "created_at": row["created_at"],
            "row_count": row["row_count"],
            "accepted_total": row["accepted_total"],
            "pending_total": row["pending_total"],
            "filename": row["filename"],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "closure_level": "L3 幂等导出（写回由官方流程完成）"}


def export(settings) -> tuple[str, str, int]:
    """返回 (csv_text, export_key, row_count)；只导出已接受且尚未导出的条目。"""
    connection = _connect(settings)
    try:
        rows = connection.execute(
            "SELECT * FROM revisions WHERE status = 'accepted' AND exported_at IS NULL "
            "ORDER BY work_id, line_no"
        ).fetchall()
        if not rows:
            return ("", "empty", 0)
        key = hashlib.sha256(
            "|".join(sorted(row["id"] for row in rows)).encode("utf-8")
        ).hexdigest()[:16]

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["作品编号", "题名", "作者", "朝代", "句序", "原句", "本地平", "本地仄", "官方平", "官方仄",
             "差异类型", "判断依据", "候选多音字", "复核备注", "条目ID"]
        )
        for row in rows:
            writer.writerow(
                [row["work_id"], row["title"], row["poet"], row["period"], row["line_no"],
                 row["line_text"], row["local_ping"], row["local_ze"], row["api_ping"], row["api_ze"],
                 row["kind"], row["reason"], row["candidates"], row["note"], row["id"]]
            )
        connection.executemany(
            "UPDATE revisions SET exported_at = datetime('now') WHERE id = ?",
            [(row["id"],) for row in rows],
        )
        # 导出记录：幂等键 + 行数 + 当时的待导出存量，供审计与回看
        _ensure_export_table(connection)
        accepted_total = connection.execute(
            "SELECT count(*) FROM revisions WHERE status = 'accepted'"
        ).fetchone()[0]
        pending_total = connection.execute(
            "SELECT count(*) FROM revisions WHERE status = 'pending'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO revision_exports (export_key, row_count, accepted_total, pending_total, filename) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, len(rows), accepted_total, pending_total, "revisions_%s.csv" % key),
        )
        connection.commit()
    finally:
        connection.close()
    return (buffer.getvalue(), key, len(rows))

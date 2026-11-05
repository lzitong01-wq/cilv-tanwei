"""评测跑分：按类别回放题库，给出整段、引文归一化与 C5 代理三类口径。

口径说明（必须与文档一致）：
- 普通题：整段逐字一致即字段全对（答案由字段渲染而来）；
- C4：引文由金标任选，公平口径是"引文归一化后整段一致"；
- C5：语义无金标，只比变化值与方向（代理字段）。
字段级断言的独立复核由 tools/benchmark_engine.py 承担，本模块不重复造。

**标准化验证集**（赛题要求：上传验证集数据，依次回复相关问题）：
- `parse_dataset()` 把上传的 JSONL / CSV 解析成统一结构，字段名按别名兼容；
- 题面**可以不带标准答案**——那种情况下只产出回答、不算分（保密验证集就是这样：
  题面与答案分两个文件），供外部拿回答去对答案；
- 带答案时照旧给三口径评分。
"""

import csv
import io
import json
import re
import threading
import time
import uuid
from pathlib import Path

from . import engine
from .errors import ApiError

QUOTE = re.compile(r"“[^”]+”")
C5_ITEM = re.compile(
    r"([甲乙])前段景物线索：[^；]+；后段主观感受线索：[^；]+；"
    r"仄声比例变化(?P<change>[+-]\d+\.\d+)个百分点，节奏转向为(?P<direction>后段上升|后段下降|前后持平)。"
)

JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

# 标准化验证集的字段别名：不同来源的验证集列名可能不同，按顺序取第一个存在的
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "qid": ("题号", "qid", "QID", "id", "ID", "题号ID"),
    "category": ("类别", "category", "题型", "type"),
    "question": ("问题", "question", "query", "prompt", "题目"),
    "gold": ("标准答案", "预期答案", "answer", "gold", "参考答案", "标准输出"),
    "boundary": ("智能体边界标签", "边界标签", "boundary"),
    "level": ("层级", "level", "tier"),
    "difficulty": ("难度", "difficulty"),
}
MAX_DATASET_BYTES = 32 * 1024 * 1024
MAX_QUESTIONS = 2000


def normalize(text: str) -> str:
    return QUOTE.sub("<Q>", text or "")


def c5_proxy(answer: str, gold: str) -> bool:
    got = {m.group(1): m.groupdict() for m in C5_ITEM.finditer(answer or "")}
    want = {m.group(1): m.groupdict() for m in C5_ITEM.finditer(gold or "")}
    if not want or set(got) != set(want):
        return False
    return all(got[label]["change"] == want[label]["change"]
               and got[label]["direction"] == want[label]["direction"] for label in want)


def load_questions(quiz_dir: Path, limit: int | None) -> list[dict]:
    path = quiz_dir / "公开测试集_700题.jsonl"
    if not path.is_file():
        raise ApiError(
            "NOT_FOUND",
            "题库不在位，无法跑分",
            {"expected_path": str(path), "hint": "设置 APP_QUIZ_DIR"},
        )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit:
        by_category: dict[str, list[dict]] = {}
        for row in rows:
            by_category.setdefault(row["类别"], []).append(row)
        per = max(1, limit // max(1, len(by_category)))
        picked = []
        for category_rows in by_category.values():
            picked.extend(category_rows[:per])
        rows = picked
    return rows


def _pick(row: dict, key: str) -> str:
    """按别名取字段值；都没有时返回空串。"""
    for alias in FIELD_ALIASES[key]:
        if alias in row and row[alias] not in (None, ""):
            return str(row[alias])
    return ""


def _parse_jsonl(text: str) -> list[dict]:
    """JSONL：每行一个 JSON 对象。行号报错，便于定位。"""
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ApiError(
                "INVALID_INPUT",
                "第 %d 行不是合法 JSON" % number,
                {"line": number, "reason": str(exc)},
            )
        if isinstance(row, dict):
            rows.append(row)
    return rows


def xlsx_to_csv(data: bytes) -> str:
    """把模板4 的 xlsx（两列：问题 / 预期答案）转成 CSV 文本。

    转文本后直接复用 `parse_dataset()` 的 CSV 分支与字段别名，
    不另写一套解析逻辑——上传格式怎么变，规范化与评分口径都只有一处。
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # 部署机没装依赖时明确报错，不静默降级
        raise ApiError(
            "KNOWLEDGE_UNAVAILABLE",
            "缺少 openpyxl，无法读取 Excel 验证集",
            {"hint": "pip install openpyxl==3.1.5（离线 wheel 在 app/deploy/wheels/）"},
        ) from exc
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheet = workbook.active
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in sheet.iter_rows(values_only=True):
            writer.writerow(["" if value is None else value for value in row])
        workbook.close()
        return buffer.getvalue()
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError("INVALID_INPUT", "Excel 验证集读取失败", {"detail": repr(exc)}) from exc


def parse_dataset(text: str, filename: str = "") -> tuple[list[dict], dict]:
    """解析标准化验证集 → (题目列表, 解析报告)。

    支持三种输入：
    - **JSONL**（每行一个 JSON 对象）——赛方题库的标准形态；
    - **CSV**（首行表头）——题库说明里写「JSONL 与 CSV 字段同步」；
    - **JSON 数组**（`[{...}, {...}]`）。

    字段名按 `FIELD_ALIASES` 兼容，题面**可以不带标准答案**——
    那种情况下 `gold` 为空串，评测只产出回答、不算分（供外部对答案）。
    """
    report = {
        "format": "",
        "total": 0,
        "with_gold": 0,
        "without_gold": 0,
        "categories": [],
        "warnings": [],
    }
    if not text or not text.strip():
        raise ApiError("INVALID_INPUT", "验证集内容为空")
    if len(text.encode("utf-8")) > MAX_DATASET_BYTES:
        raise ApiError(
            "INVALID_INPUT",
            "验证集文件过大",
            {"bytes": len(text.encode("utf-8")), "limit": MAX_DATASET_BYTES},
        )

    rows: list[dict] = []
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except ValueError as exc:
            raise ApiError("INVALID_INPUT", "验证集不是合法 JSON 数组", {"reason": str(exc)})
        if not isinstance(payload, list):
            raise ApiError("INVALID_INPUT", "验证集 JSON 顶层必须是数组")
        rows = [row for row in payload if isinstance(row, dict)]
        report["format"] = "json"
    elif stripped.startswith("{"):
        # 可能是单个 JSON 对象、也可能是 {"questions": [...]} 包了一层，
        # 也可能是 JSONL（首行是 { 但整体不是合法 JSON）——按整体解析的成败区分。
        payload = None
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None
        if isinstance(payload, list):
            rows = [row for row in payload if isinstance(row, dict)]
            report["format"] = "json"
        elif isinstance(payload, dict):
            inner = payload.get("questions") or payload.get("data")
            rows = (
                [row for row in inner if isinstance(row, dict)]
                if isinstance(inner, list)
                else [payload]
            )
            report["format"] = "json"
        else:
            rows = _parse_jsonl(text)
            report["format"] = "jsonl"
    elif filename.lower().endswith(".csv") or ("," in text.splitlines()[0] and "\t" not in text.splitlines()[0]):
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader]
        report["format"] = "csv"
    else:
        rows = _parse_jsonl(text)
        report["format"] = "jsonl"

    if not rows:
        raise ApiError("INVALID_INPUT", "验证集里没有题目")
    if len(rows) > MAX_QUESTIONS:
        raise ApiError(
            "INVALID_INPUT",
            "验证集题数超过上限",
            {"questions": len(rows), "limit": MAX_QUESTIONS},
        )

    normalized = []
    categories: dict[str, int] = {}
    missing_question = 0
    for index, row in enumerate(rows, 1):
        question = _pick(row, "question").strip()
        if not question:
            missing_question += 1
            continue
        gold = _pick(row, "gold").strip()
        category = _pick(row, "category").strip() or "未分类"
        categories[category] = categories.get(category, 0) + 1
        normalized.append(
            {
                "qid": _pick(row, "qid").strip() or ("Q%04d" % index),
                "category": category,
                "question": question,
                "gold": gold,
                "boundary": _pick(row, "boundary").strip(),
                "level": _pick(row, "level").strip(),
                "difficulty": _pick(row, "difficulty").strip(),
            }
        )
    if missing_question:
        report["warnings"].append("%d 行缺少问题字段，已跳过" % missing_question)
    if not normalized:
        raise ApiError("INVALID_INPUT", "验证集里没有可用题目（缺少问题字段）")

    report["total"] = len(normalized)
    report["with_gold"] = sum(1 for row in normalized if row["gold"])
    report["without_gold"] = report["total"] - report["with_gold"]
    report["categories"] = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    if report["without_gold"]:
        report["warnings"].append(
            "%d 题没有标准答案，将只产出回答、不计算分数" % report["without_gold"]
        )
    return normalized, report


def evaluate(store, quiz_dir: Path, limit: int | None = None, progress=None) -> dict:
    rows = load_questions(quiz_dir, limit)
    return evaluate_rows(store, rows, limit=None, progress=progress, source="本地题库")


def evaluate_rows(store, rows: list[dict], limit: int | None = None, progress=None,
                  source: str = "上传验证集") -> dict:
    """逐题调用引擎，给出回答与（若有标准答案）三口径评分。

    `rows` 是 `parse_dataset()` 规范化后的题目；**没有标准答案的题目**照样产出回答，
    但三个口径记为 None（不是 False）——「没标准答案」和「答错了」是两回事。
    """
    if limit:
        rows = rows[:limit]
    started = time.perf_counter()
    items = []
    by_category: dict[str, dict] = {}
    errors = 0
    scored = 0
    for index, row in enumerate(rows, 1):
        category = row.get("类别") or row.get("category") or "未知"
        gold = row.get("gold") or row.get("标准答案") or ""
        bucket = by_category.setdefault(
            category,
            {"questions": 0, "raw": 0, "normalized": 0, "proxy": 0, "errors": 0, "total_ms": 0.0},
        )
        item_started = time.perf_counter()
        try:
            result = engine.solve(row.get("问题") or row.get("question") or "", store)
            answer = result.get("answer") or ""
            if gold:
                raw_ok = answer == gold
                normalized_ok = normalize(answer) == normalize(gold)
                proxy_ok = c5_proxy(answer, gold) if category == engine.CATEGORY_C5 else normalized_ok
                scored += 1
            else:
                raw_ok = normalized_ok = proxy_ok = None
            bucket["questions"] += 1
            bucket["raw"] += int(raw_ok) if raw_ok is not None else 0
            bucket["normalized"] += int(normalized_ok) if normalized_ok is not None else 0
            bucket["proxy"] += int(proxy_ok) if proxy_ok is not None else 0
            item_ms = (time.perf_counter() - item_started) * 1000
            bucket["total_ms"] += item_ms
            items.append(
                {
                    "qid": row.get("题号") or row.get("qid"),
                    "question": row.get("问题") or row.get("question") or "",
                    "elapsed_ms": round(item_ms, 1),
                    "category": category,
                    "status": "ok",
                    "raw": raw_ok,
                    "normalized": normalized_ok,
                    "proxy": proxy_ok,
                    "answer": answer,
                    "gold": gold,
                    "fields": result.get("fields") or {},
                    "evidence": len(result.get("evidence") or []),
                    "error": None,
                }
            )
        except ApiError as exc:
            errors += 1
            bucket["errors"] += 1
            items.append(
                {
                    "qid": row.get("题号") or row.get("qid"),
                    "question": row.get("问题") or row.get("question") or "",
                    "elapsed_ms": round((time.perf_counter() - item_started) * 1000, 1),
                    "category": category,
                    "status": exc.code,
                    "raw": False if gold else None,
                    "normalized": False if gold else None,
                    "proxy": False if gold else None,
                    "answer": "",
                    "gold": gold,
                    "fields": {},
                    "evidence": 0,
                    "error": exc.message,
                }
            )
        if progress is not None:
            progress(index)
    elapsed = (time.perf_counter() - started) * 1000

    total = len(items)
    summary = {
        "questions": total,
        "source": source,
        "scored": scored,
        "unscored": total - scored,
        "raw_passed": sum(1 for item in items if item["raw"]),
        "normalized_passed": sum(1 for item in items if item["normalized"]),
        "proxy_passed": sum(1 for item in items if item["proxy"]),
        "errors": errors,
        "elapsed_ms": round(elapsed, 1),
        "avg_ms": round(elapsed / total, 2) if total else 0,
        "by_category": {
            category: {
                **bucket,
                "avg_ms": round(bucket["total_ms"] / bucket["questions"], 2) if bucket["questions"] else 0,
            }
            for category, bucket in by_category.items()
        },
    }
    return {"summary": summary, "items": items}


def start(store, quiz_dir: Path, limit: int | None) -> str:
    with _LOCK:
        running = [job for job in JOBS.values() if job["state"] == "running"]
        if running:
            raise ApiError("RATE_LIMITED", "已有跑分任务在运行，请等它结束")
        job_id = "job_%s" % uuid.uuid4().hex[:10]
        JOBS[job_id] = {
            "id": job_id,
            "state": "running",
            "progress": 0,
            "started_at": time.time(),
            "summary": None,
            "items": [],
            "error": None,
        }
    thread = threading.Thread(target=_run, args=(job_id, store, quiz_dir, limit), daemon=True)
    thread.start()
    return job_id


def start_dataset(store, rows: list[dict], limit: int | None = None, report: dict | None = None) -> str:
    """上传验证集后起任务：逐题调用内部接口作答。

    这是赛题要求的「上传验证集数据 → 依次回复相关问题」的落点：
    解析在 `parse_dataset()` 做，这里只负责跑。
    """
    with _LOCK:
        running = [job for job in JOBS.values() if job["state"] == "running"]
        if running:
            raise ApiError("RATE_LIMITED", "已有跑分任务在运行，请等它结束")
        job_id = "job_%s" % uuid.uuid4().hex[:10]
        JOBS[job_id] = {
            "id": job_id,
            "state": "running",
            "progress": 0,
            "started_at": time.time(),
            "summary": None,
            "items": [],
            "error": None,
            "report": report or {},
        }
    thread = threading.Thread(
        target=_run_rows, args=(job_id, store, rows, limit, report), daemon=True
    )
    thread.start()
    return job_id


def _run_rows(job_id: str, store, rows: list[dict], limit: int | None, report: dict | None) -> None:
    try:
        outcome = evaluate_rows(
            store,
            rows,
            limit=limit,
            progress=lambda count: JOBS[job_id].__setitem__("progress", count),
            source="上传验证集（%s）" % ((report or {}).get("format") or "jsonl"),
        )
        JOBS[job_id].update(
            {
                "state": "done",
                "summary": outcome["summary"],
                "items": outcome["items"],
                "progress": len(outcome["items"]),
            }
        )
    except ApiError as exc:
        JOBS[job_id].update({"state": "failed", "error": {"code": exc.code, "message": exc.message}})
    except Exception as exc:  # 兜底：任务失败必须落地，不能静默
        JOBS[job_id].update({"state": "failed", "error": {"code": "INTERNAL", "message": repr(exc)}})


def _export_xlsx(job_id: str, items: list[dict]) -> tuple:
    """按模板5 导出五列：问题 | 预期答案 | 智能体实际回复 | 耗时(秒) | 状态。"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as exc:
        raise ApiError(
            "KNOWLEDGE_UNAVAILABLE",
            "缺少 openpyxl，无法导出 Excel",
            {"hint": "pip install openpyxl==3.1.5（离线 wheel 在 app/deploy/wheels/）"},
        ) from exc
    book = Workbook()
    sheet = book.active
    sheet.title = "验证集结果"
    headers = ["问题", "预期答案", "智能体实际回复", "耗时(秒)", "状态"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="8C2F27")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for item in items:
        status = item.get("status") or ""
        if status == "ok":
            label = "正常"
        elif status in ("TIMEOUT", "timeout"):
            label = "超时"
        elif status:
            label = "报错"
        else:
            label = "未评分"
        answer = item.get("answer") or ""
        sheet.append([
            item.get("question") or "",
            item.get("gold") or "",
            answer if answer else "（空）",
            round((item.get("elapsed_ms") or 0) / 1000, 2),
            label,
        ])
    widths = [42, 50, 50, 12, 12]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    sheet.freeze_panes = "A2"
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    buffer = io.BytesIO()
    book.save(buffer)
    return (
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "验证集结果_%s.xlsx" % job_id,
    )


def export_items(job_id: str, fmt: str = "jsonl") -> tuple:
    """导出逐题回答（供外部拿回答去对答案）。

    返回 (内容, MIME, 文件名)。格式 `jsonl` / `csv` 返回 str，
    `xlsx`（模板5 五列）返回 bytes。
    """
    job = JOBS.get(job_id)
    if job is None:
        raise ApiError("NOT_FOUND", "跑分任务不存在", {"job_id": job_id})
    items = job["items"]
    if fmt == "xlsx":
        return _export_xlsx(job_id, items)
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["题号", "类别", "状态", "回答", "标准答案", "字段JSON", "错误"])
        for item in items:
            writer.writerow(
                [
                    item.get("qid") or "",
                    item.get("category") or "",
                    item.get("status") or "",
                    item.get("answer") or "",
                    item.get("gold") or "",
                    json.dumps(item.get("fields") or {}, ensure_ascii=False),
                    item.get("error") or "",
                ]
            )
        return (
            ("\ufeff" + buffer.getvalue()),
            "text/csv; charset=utf-8",
            "验证集回答_%s.csv" % job_id,
        )
    lines = []
    for item in items:
        lines.append(
            json.dumps(
                {
                    "题号": item.get("qid") or "",
                    "类别": item.get("category") or "",
                    "状态": item.get("status") or "",
                    "回答": item.get("answer") or "",
                    "标准答案": item.get("gold") or "",
                    "字段": item.get("fields") or {},
                    "错误": item.get("error") or "",
                },
                ensure_ascii=False,
            )
        )
    return (
        "\n".join(lines) + ("\n" if lines else ""),
        "application/x-ndjson; charset=utf-8",
        "验证集回答_%s.jsonl" % job_id,
    )


def _run(job_id: str, store, quiz_dir: Path, limit: int | None) -> None:
    try:
        outcome = evaluate(
            store,
            quiz_dir,
            limit,
            progress=lambda count: JOBS[job_id].__setitem__("progress", count),
        )
        JOBS[job_id].update(
            {
                "state": "done",
                "summary": outcome["summary"],
                "items": outcome["items"],
                "progress": len(outcome["items"]),
            }
        )
    except ApiError as exc:
        JOBS[job_id].update({"state": "failed", "error": {"code": exc.code, "message": exc.message}})
    except Exception as exc:  # 兜底：任务失败必须落地，不能静默
        JOBS[job_id].update({"state": "failed", "error": {"code": "INTERNAL", "message": repr(exc)}})


def get(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise ApiError("NOT_FOUND", "跑分任务不存在", {"job_id": job_id})
    return {
        "id": job["id"],
        "state": job["state"],
        "progress": job["progress"],
        "summary": job["summary"],
        "error": job["error"],
        "elapsed_ms": round((time.time() - job["started_at"]) * 1000, 0),
    }


def get_items(job_id: str, page: int, page_size: int) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise ApiError("NOT_FOUND", "跑分任务不存在", {"job_id": job_id})
    rows = job["items"]
    start_index = (page - 1) * page_size
    return {
        "items": rows[start_index : start_index + page_size],
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "state": job["state"],
    }

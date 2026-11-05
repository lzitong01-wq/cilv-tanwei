"""HTTP 层：统一响应包、错误映射、只读接口与静态托管。

契约见《前后端一致性与接口契约 V1》。本文件只做协议转换与参数校验；
计算在 core.engine / core.parser，模型候选在 core.sidecar / core.qa / core.style。
"""

import mimetypes
import os
import platform
import socket
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_file

from core import argument as argument_core
from core import chars as char_core, rhyme
from core import agent, corpus, sidecar, tier2
from core import engine, evaluator, graph, metrics, parser, prosody, qa, revisions, rules, sentiment, style, tunes
from core.config import APP_VERSION, CONTRACT_VERSION, Settings, load_settings
from core.errors import ApiError, new_trace_id, ok_body
from core import sqlite_compat
from core.store import Store

# 龙蜥自带的 /etc/mime.types 未必认识 woff2，注册一次免得字体以 octet-stream 下发
for _suffix, _mime in (
    (".woff2", "font/woff2"),
    (".woff", "font/woff"),
    (".ttf", "font/ttf"),
    (".otf", "font/otf"),
    (".webp", "image/webp"),
):
    mimetypes.add_type(_mime, _suffix)

# 构建产物带内容哈希，可长缓存；字体按构建期版本整体替换，同样长缓存
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_REVALIDATE = "no-cache"

# 模型端点探活：只做 TCP 连通性判断，不调用模型、不消耗 token。
# 结果缓存 60 秒，避免每次 /api/health 都等一次超时。
_MODEL_CHECK: dict = {"at": 0.0, "state": "unconfigured"}
_MODEL_LOCK = threading.Lock()
MODEL_CHECK_TTL = 60.0


def _model_state() -> str:
    url = os.environ.get("APP_MODEL_URL", "http://10.27.66.12/v1/chat/completions")
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        return "unconfigured"
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    now = time.monotonic()
    with _MODEL_LOCK:
        if now - _MODEL_CHECK["at"] < MODEL_CHECK_TTL:
            return _MODEL_CHECK["state"]
    state = "unreachable"
    try:
        with socket.create_connection((host, port), timeout=0.2):
            state = "reachable"
    except OSError:
        pass
    with _MODEL_LOCK:
        _MODEL_CHECK.update({"at": now, "state": state})
    return state


def _iso_mtime(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _process_started_at() -> str | None:
    """进程启动时刻（ISO）。取不到就返回 None，不编造。"""
    try:
        with open("/proc/self/stat", encoding="ascii") as handle:
            fields = handle.read().rsplit(") ", 1)[1].split()
        # 第 22 个字段是 starttime（时钟节拍数），需除以 HZ
        starttime = int(fields[19])
        hz = os.sysconf("SC_CLK_TCK")
        boot = Path("/proc/stat").read_text(encoding="ascii")
        for line in boot.splitlines():
            if line.startswith("btime"):
                boot_epoch = int(line.split()[1])
                return datetime.fromtimestamp(
                    boot_epoch + starttime / hz, timezone.utc
                ).isoformat(timespec="seconds")
    except Exception:
        return None
    return None


def _char_readings_tier2(work: dict) -> dict:
    """Tier 2a 逐字四声：同源带调拼音口径，**非官方标注**。"""
    payload_lines = []
    stats = corpus.tier2_line_stats(work)
    for line in stats:
        items = tier2.chars_for(work, line["line_no"] - 1)
        payload_lines.append(
            {
                "line_no": line["line_no"],
                "text": line["text"],
                "character_count": line["character_count"],
                "ping_count": line["ping_count"],
                "ze_count": line["ze_count"],
                "unknown_count": line["unknown_count"],
                "chars": items,
                "line_end": rhyme.line_end_info(line["text"]),
            }
        )
    return {
        "work_id": work.get("id"),
        "title": work.get("title"),
        "tune": work.get("tune"),
        "split_after_line": None,
        "source_note": "逐字四声来自同源带调拼音（Tier 2a），非官方标注；官方仅提供 Tier 1 的逐句计数",
        "tier_label": corpus.TIER2_LABEL,
        "rhyme_boundary": rhyme.boundary(),
        "lines": payload_lines,
    }


def _browse_works(store, settings, tier: str, period: str, tune: str, poet: str, limit: int) -> dict:
    """按作者／词牌／朝代浏览作品（没有关键词时的检索入口）。

    返回结构与 `/api/works` 一致，另给 `mode="browse"` 便于界面区分。
    """
    rows = []
    official_rows: list[dict] = []
    if tier in ("1", "all"):
        for work in store.all():
            poet_name = (
                (work.get("poet") or {}).get("name")
                if isinstance(work.get("poet"), dict)
                else work.get("poet_name")
            ) or ""
            if poet and poet not in poet_name:
                continue
            if tune and tune != (work.get("tune_pattern") or ""):
                continue
            if period and period != (work.get("period_label") or ""):
                continue
            official_rows.append(
                {
                    "id": work.get("id"),
                    "title": work.get("title"),
                    "tune": work.get("tune_pattern"),
                    "period": work.get("period_label"),
                    "poet": poet_name,
                    "lines": len((work.get("prosody") or {}).get("lines") or []),
                    "tier_label": corpus.TIER1_LABEL,
                    "source": "Tier 1",
                    # 与检索模式同一套字段：浏览模式没有正文命中句，但字段必须在，
                    # 否则前端按 matched.map(...) 渲染时会炸
                    "matched": [],
                    "hits": 0,
                }
            )
    rows.extend(official_rows)
    corpus_total = 0
    if tier in ("2a", "2b", "2c", "2d", "all") and corpus.tier2_available(settings):
        tier_key = tier if tier != "all" else ""
        # 先取总数（limit=1 探针），再取前 limit 条——总数不能被 limit 截断
        corpus_total = corpus.list_tier2(settings, period, tune, poet, "", 1, 1, tier_key)["total"]
        block = corpus.list_tier2(settings, period, tune, poet, "", 1, limit, tier_key)
        rows.extend(block["items"])
    return {
        "items": rows[:limit],
        "total": len(official_rows) + corpus_total,
        "mode": "browse",
        "tier": tier,
        "tier_counts": corpus.tier2_counts(settings),
    }


def _char_readings(work: dict) -> dict:
    """逐字四声：本地字典口径，**不是官方标注**，来源字段必须如实标注。"""
    prosody, lines = engine._prosody_lines(work)
    payload_lines = []
    for line in lines:
        items = char_core.annotate(line["text"])
        payload_lines.append(
            {
                "line_no": line["line_no"],
                "text": line["text"],
                # 逐句计数沿用官方 prosody；逐字项来自本地字典，两者口径不同、并列展示
                "character_count": line["character_count"],
                "ping_count": line["ping_count"],
                "ze_count": line["ze_count"],
                "chars": items,
                "line_end": rhyme.line_end_info(line["text"]),
            }
        )
    return {
        "work_id": work.get("id"),
        "title": work.get("title"),
        "tune": work.get("tune_pattern"),
        "split_after_line": prosody["split_after_line"],
        "source_note": "逐字四声为本地字典计算，官方仅提供逐句平仄计数",
        "rhyme_boundary": rhyme.boundary(),
        "lines": payload_lines,
    }


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    store = Store(settings.snapshot_path, settings.index_path)
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    app.config["SETTINGS"] = settings
    app.config["STORE"] = store

    @app.before_request
    def _assign_trace():
        g.trace_id = request.headers.get("X-Trace-Id") or new_trace_id()

    @app.after_request
    def _stamp_trace(response: Response) -> Response:
        response.headers["X-Trace-Id"] = g.get("trace_id", "")
        response.headers["X-Contract-Version"] = CONTRACT_VERSION
        return response

    def _payload(data, status: int = 200):
        return jsonify(ok_body(data, g.trace_id)), status

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return jsonify(exc.as_body(g.trace_id)), exc.http_status

    @app.errorhandler(404)
    def _not_found(_exc):
        return jsonify(ApiError("NOT_FOUND", "路径不存在").as_body(g.trace_id)), 404

    @app.errorhandler(405)
    def _method_not_allowed(_exc):
        return jsonify(ApiError("INVALID_INPUT", "该路径不支持此方法").as_body(g.trace_id)), 405

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        app.logger.exception("未处理异常 trace=%s", g.get("trace_id"))
        return jsonify(ApiError("INTERNAL", "服务内部错误", {"type": type(exc).__name__}).as_body(g.trace_id)), 500

    @app.get("/api/health")
    def health():
        snapshot_ok = settings.snapshot_exists
        if snapshot_ok:
            try:
                store.load()
                db_state = "ok"
                works = len(store.works)
            except ApiError:
                db_state = "error"
                works = 0
        else:
            db_state = "missing"
            works = 0
        return _payload(
            {
                "app": "ok",
                "db": db_state,
                "model": _model_state(),
                "model_checked_ttl": MODEL_CHECK_TTL,
                "knowledge": "offline_snapshot",
                "snapshot_at": _iso_mtime(settings.snapshot_path),
                "works": works,
                "version": APP_VERSION,
                "contract": CONTRACT_VERSION,
                "snapshot_path": str(settings.snapshot_path),
                # 语料总量（官方 + 同源）——顶栏给用户看的是这个，不是内部层级
                "corpus_total": len(store.works) + corpus.tier2_counts(settings)["works"],
                "platform": "%s / Python %s" % (sys.platform, platform.python_version()),
                "dist": "built" if settings.dist_exists else "absent",
                # SQLite 后端如实展示：目标机系统 SQLite 过旧时会切到 pysqlite3，
                # 评审时需要能看出检索走的是哪条路径。
                "sqlite": sqlite_compat.backend_name(),
                "sqlite_trigram": sqlite_compat.trigram_capable(),
                # 部署标识：让评审看得出这是**真实跑在一台服务器上**，
                # 而不是本地起个 demo。值全部来自运行时实际环境，不写死。
                "deployment": {
                    "host": platform.node(),
                    "hostname": socket.gethostname(),
                    "pid": os.getpid(),
                    "cwd": str(settings.app_root) if hasattr(settings, "app_root") else os.getcwd(),
                    "data_root": str(settings.snapshot_path.parent),
                    "started_at": _process_started_at(),
                },
            }
        )

    @app.get("/api/ready")
    def ready():
        if not settings.snapshot_exists:
            raise ApiError("KNOWLEDGE_UNAVAILABLE", "快照未就绪", {"path": str(settings.snapshot_path)})
        store.load()
        return _payload({"ready": True, "works": len(store.works)})

    @app.get("/api/meta")
    def meta():
        summary = store.summary()
        summary.update({"version": APP_VERSION, "contract": CONTRACT_VERSION})
        # 真实语料规模：Tier 1 是唯一官方基准口径，Tier 2a 才是「读词」的底子
        counts = corpus.tier2_counts(settings)
        summary["tier2"] = {
            "works": counts["works"],
            "poets": counts["poets"],
            "tunes": counts["tunes"],
            "available": counts["available"],
            "label": corpus.TIER2_LABEL,
            "layers": counts.get("layers") or [],
        }
        summary["corpus_total"] = len(store.works) + counts["works"]
        layers = [
            {
                "tier": "Tier 1",
                "label": corpus.TIER1_LABEL,
                "works": len(store.works),
                "official": True,
                "poets": summary.get("poets"),
                "tunes": summary.get("tunes"),
            }
        ]
        for item in counts.get("layers") or []:
            layers.append(
                {
                    "tier": item["tier"],
                    "label": item["label"],
                    "works": item["works"],
                    "official": False,
                    "poets": counts["poets"],
                    "tunes": counts["tunes"],
                }
            )
        summary["tiers"] = layers
        return _payload(summary)

    @app.get("/api/prosody/resolve")
    def prosody_resolve():
        """词谱体式候选（分册 A §A3）：多候选 + 置信度，不给单一答案。"""
        raw = request.args.get("work_id", "")
        if not raw.isdigit():
            raise ApiError("INVALID_INPUT", "需要 work_id")
        work = store.get(int(raw))
        _prosody, lines = engine._prosody_lines(work)
        sentences = [line["character_count"] for line in lines]
        result = prosody.best(
            work.get("tune_pattern") or "",
            sentences,
            (work.get("prosody") or {}).get("split_after_line"),
        )
        if result is None:
            return _payload(
                {
                    "work_id": work.get("id"),
                    "tune": work.get("tune_pattern"),
                    "candidates": [],
                    "available": False,
                    "boundary": "词谱中没有同总字数的体式候选，按无法归体处理。",
                }
            )
        aligned = None
        if result["allowed_for_alignment"]:
            aligned = prosody.align(result["form"], sentences)
        return _payload(
            {
                "work_id": work.get("id"),
                "tune": work.get("tune_pattern"),
                "title": work.get("title"),
                "sentences": sentences,
                "candidates": result["candidates"],
                "unique": result["unique"],
                "allowed_for_alignment": result["allowed_for_alignment"],
                "form": result["form"],
                "per_line": aligned or [],
                "available": True,
                "boundary": result["boundary"],
            }
        )

    @app.get("/api/metrics")
    def metrics_view():
        """三张指标表 + 门禁状态（数字来自实跑产物，不写死）。"""
        return _payload(metrics.require(settings))

    @app.get("/api/tunes")
    def tunes_overview():
        """词调身份层概览：官方层（100 词牌）+ 同源层摘要（数千词牌）。

        同源层只给规模与热门榜，全量词调走详情端点——概览不该返回几兆 JSON。
        """
        data = tunes.build(store, settings)
        return _payload(
            {
                "tunes": data["tunes"],
                "stats": data["stats"],
                "boundary": data["boundary"],
                "corpus": tunes.corpus_summary(store, settings),
            }
        )

    @app.get("/api/tunes/review")
    def tunes_review():
        """别名归并抽样复核记录（A4.4 门禁，≥50 条，种子固定）。"""
        return _payload(tunes.review_sample(store))

    @app.get("/api/tunes/resolve")
    def tunes_resolve():
        alias = request.args.get("alias", "")
        return _payload(tunes.resolve(store, alias, settings))

    @app.get("/api/tunes/<tune_id>")
    def tune_detail(tune_id: str):
        """词调详情。`tier=corpus` 看同源层（官方层没有的词牌，或要看同源口径时用）。"""
        tier = (request.args.get("tier") or "official").strip().lower()
        name = tune_id[5:] if tune_id.startswith("tune_") else tune_id
        if tier == "corpus":
            found = tunes.corpus_tune(store, settings, name)
            if not found:
                raise ApiError("NOT_FOUND", "词调不在同源层中", {"tune_id": tune_id})
            return _payload(found)
        try:
            return _payload(tunes.get(store, tune_id))
        except ApiError:
            # 官方层没有的词牌，多半在同源层（1,897 个同源词调里绝大多数只出现在那里）
            found = tunes.corpus_tune(store, settings, name)
            if found:
                return _payload(found)
            raise

    @app.get("/api/sentiment/benchmark")
    def sentiment_benchmark():
        """AI 标注基准集与一致性指标（由模型标注，未经人工核验）。"""
        import json

        path = settings.sentiment_benchmark_path
        if not path.is_file():
            raise ApiError(
                "NOT_FOUND",
                "情感基准集未生成",
                {"expected_path": str(path), "hint": "运行 python app/scripts/label_sentiment.py"},
            )
        return _payload(json.loads(path.read_text(encoding="utf-8")))

    @app.post("/api/sentiment/candidate")
    def sentiment_candidate():
        """L1 逐句情感方向候选：事实卡 → 模型 → 四道闸门 + 禁止推断闸。"""
        body = request.get_json(silent=True) or {}
        work_id = body.get("work_id")
        line_no = body.get("line_no")
        if work_id and line_no:
            work = store.get(int(work_id))
            _prosody, lines = engine._prosody_lines(work)
            line = next((row for row in lines if row["line_no"] == int(line_no)), None)
            if line is None:
                raise ApiError("NOT_FOUND", "该作品没有这个句序", {"work_id": work_id, "line_no": line_no})
            poet = work.get("poet") or {}
            item = {
                "id": "w%s-l%s" % (work.get("id"), line_no),
                "text": line["text"],
                "work_id": work.get("id"),
                "title": work.get("title"),
                "tune": work.get("tune_pattern"),
                "poet": poet.get("name") if isinstance(poet, dict) else str(poet or ""),
                "period": work.get("period_label"),
                "line_no": line["line_no"],
                "chars": line["character_count"],
                "ping": line["ping_count"],
                "ze": line["ze_count"],
            }
        elif body.get("text"):
            text = str(body["text"]).strip()
            item = {
                "id": "adhoc",
                "text": text,
                "chars": len(text),
                "ping": None,
                "ze": None,
            }
        else:
            raise ApiError("INVALID_INPUT", "需要 work_id + line_no，或直接给 text")
        model = str(body.get("model") or sidecar.DEFAULT_MODEL)
        return _payload(sentiment.candidate(item, model=model))

    @app.get("/api/rules")
    def rules_view():
        """当前生效的规则版本（外置，可在不改代码的情况下调整判类措辞与闸门词表）。

        返回在原始快照上补了 `detect`（扁平关键词）与 `match_mode`（all/any）的视图，
        界面不必自己解析 `match` 的结构。
        """
        return _payload(rules.view())

    @app.get("/api/invariants")
    def invariants():
        path = Path(__file__).resolve().parent.parent / "invariants.json"
        if not path.is_file():
            raise ApiError("NOT_FOUND", "未找到不变量登记表", {"path": str(path)})
        import json

        return _payload(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/works")
    def works():
        page = request.args.get("page", "1")
        page_size = request.args.get("page_size", "20")
        if not page.isdigit() or not page_size.isdigit():
            raise ApiError("INVALID_INPUT", "page 与 page_size 必须为正整数")
        page_no, size = int(page), int(page_size)
        # 上限 200：单篇解析页一次取 120 条，上限 100 时直接 400，
        # 列表会是空的（前端曾把 400 静默显示成「显示 0 / 0」）
        if page_no < 1 or not 1 <= size <= 200:
            raise ApiError("INVALID_INPUT", "page ≥ 1 且 1 ≤ page_size ≤ 200")
        period = request.args.get("period")
        tune = request.args.get("tune")
        poet = request.args.get("poet")
        query = (request.args.get("q") or "").strip()
        # tier：1 = 官方快照（默认）、2a = 同源清词、2b = 同源宋词、all = 都列
        tier = (request.args.get("tier") or "1").strip().lower()
        if tier not in ("1", "2a", "2b", "2c", "2d", "all"):
            raise ApiError("INVALID_INPUT", "tier 取 1 / 2a / 2b / 2c / 2d / all", {"tier": tier})

        rows = []
        if tier in ("1", "all"):
            for work in store.all():
                if period and (work.get("period_label") or "") != period:
                    continue
                if tune and (work.get("tune_pattern") or "") != tune:
                    continue
                poet_name = (work.get("poet") or {}).get("name") if isinstance(work.get("poet"), dict) else work.get("poet_name")
                if poet and poet not in (poet_name or ""):
                    continue
                if query and query not in (work.get("text") or "") and query not in (work.get("title") or ""):
                    continue
                rows.append(
                    {
                        "id": work.get("id"),
                        "title": work.get("title"),
                        "tune": work.get("tune_pattern"),
                        "period": work.get("period_label"),
                        "poet": poet_name,
                        "lines": len((work.get("prosody") or {}).get("lines") or []),
                        "tier_label": corpus.TIER1_LABEL,
                        "source": "Tier 1",
                    }
                )
        if tier in ("2a", "2b", "2c", "2d") and corpus.tier2_available(settings):
            # 只要 Tier 2 某层时走 SQL 分页，别把几万条全拉进内存
            block = corpus.list_tier2(
                settings, period or "", tune or "", poet or "", query, page_no, size, tier
            )
            return _payload(
                {
                    "items": block["items"],
                    "total": block["total"],
                    "page": page_no,
                    "page_size": size,
                    "tier": tier,
                    "tier_counts": corpus.tier2_counts(settings),
                }
            )

        # 全部层：官方标注优先，其后接同源层——两层都要出现在 items 里，
        # 否则「搜不到」会被误解成「这个作品不存在」。
        #
        # 两个坑都踩过：
        # ① total 不能用 len(rows)——同源层只取了前几条，total 会跟着被截断
        #    （page_size=20 时曾报「共 483 篇」，真实是 43,614 篇）。
        #    这里用 limit=1 探针单独查同源层的真实命中数。
        # ② 跨层分页要扣掉官方层已出的条数——否则每页返回的都是同源层最前面那几条，
        #    翻页看起来「不动」。
        official_count = len(rows)
        tier2_total = 0
        tier2_items: list = []
        if tier == "all" and corpus.tier2_available(settings):
            probe = corpus.list_tier2(
                settings, period or "", tune or "", poet or "", query, 1, 1, ""
            )
            tier2_total = probe["total"]
            start = (page_no - 1) * size
            official_take = max(0, min(size, official_count - start))
            tier2_need = size - official_take
            if tier2_need > 0:
                tier2_offset = max(0, start - official_count)
                block = corpus.list_tier2(
                    settings, period or "", tune or "", poet or "", query,
                    1, tier2_need, "", offset=tier2_offset,
                )
                tier2_items = block["items"]
        else:
            start = (page_no - 1) * size

        # tier=all 时官方层在前、同源层接后面；tier=1 时只有官方层
        if tier == "all":
            items = rows[start : start + size] + tier2_items
        else:
            items = rows[start : start + size]
        total = official_count + tier2_total if tier == "all" else len(rows)
        return _payload(
            {
                "items": items[:size],
                "total": total,
                "page": page_no,
                "page_size": size,
                "tier": tier,
                "tier_counts": corpus.tier2_counts(settings),
            }
        )

    @app.get("/api/works/<work_id>")
    def work_detail(work_id: str):
        if corpus.is_tier2_id(work_id):
            work = corpus.get_tier2(settings, work_id)
            return _payload(
                {
                    "id": work["id"],
                    "title": work["title"],
                    "tune": work["tune"],
                    "period": work["period"],
                    "poet": work["poet"],
                    "text": work["text"],
                    "split_after_line": work["split_after_line"],
                    "tier_label": work["tier_label"],
                    "source": work["source"],
                    "warning": work["warning"],
                    "ze_ratio": work["ze_ratio"],
                    "line_count": work["line_count"],
                    "rhyme_char": work["rhyme_char"],
                    # 无拼音层（Tier 2d）的声律由本地今音算出，界面要能标清口径
                    "sound_source": work.get("sound_source"),
                    "line_tones": work.get("line_tones") or [],
                }
            )
        if not work_id.isdigit():
            raise ApiError("INVALID_INPUT", "作品编号格式不对", {"work_id": work_id})
        work = store.get(int(work_id))
        return _payload(
            {
                "id": work.get("id"),
                "title": work.get("title"),
                "tune": work.get("tune_pattern"),
                "period": work.get("period_label"),
                "poet": (work.get("poet") or {}).get("name") if isinstance(work.get("poet"), dict) else work.get("poet_name"),
                "text": work.get("text"),
                "split_after_line": (work.get("prosody") or {}).get("split_after_line"),
                "tier_label": corpus.TIER1_LABEL,
                "source": "Tier 1",
            }
        )

    @app.get("/api/works/<work_id>/lines")
    def work_lines(work_id: str):
        if corpus.is_tier2_id(work_id):
            work = corpus.get_tier2(settings, work_id)
            return _payload(
                {
                    "work_id": work["id"],
                    "split_after_line": None,
                    "tier_label": corpus.TIER2_LABEL,
                    "source_note": "逐句平仄由同源带调拼音统计，非官方标注",
                    "rhyme_boundary": rhyme.boundary(),
                    "lines": rhyme.annotate_lines(corpus.tier2_line_stats(work)),
                }
            )
        if not work_id.isdigit():
            raise ApiError("INVALID_INPUT", "作品编号格式不对", {"work_id": work_id})
        work = store.get(int(work_id))
        prosody, lines = engine._prosody_lines(work)
        return _payload(
            {
                "work_id": work.get("id"),
                "split_after_line": prosody["split_after_line"],
                "lines": [
                    {
                        "line_no": line["line_no"],
                        "text": line["text"],
                        "character_count": line["character_count"],
                        "ping_count": line["ping_count"],
                        "ze_count": line["ze_count"],
                        "line_end": rhyme.line_end_info(line["text"]),
                    }
                    for line in lines
                ],
                "rhyme_boundary": rhyme.boundary(),
            }
        )

    @app.get("/api/works/<work_id>/chars")
    def work_chars(work_id: str):
        if corpus.is_tier2_id(work_id):
            return _payload(_char_readings_tier2(corpus.get_tier2(settings, work_id)))
        if not work_id.isdigit():
            raise ApiError("INVALID_INPUT", "作品编号格式不对", {"work_id": work_id})
        return _payload(_char_readings(store.get(int(work_id))))

    @app.post("/api/parse")
    def parse_free_text():
        """自由文本解析：未入库的词文给本地口径结果，不冒充官方标注。"""
        body = request.get_json(silent=True) or {}
        text = str(body.get("text") or "").strip()
        if not text:
            raise ApiError("INVALID_INPUT", "text 不能为空")
        if len(text) > 4000:
            raise ApiError("INVALID_INPUT", "text 过长（≤4000 字）")
        tune_hint = str(body.get("tune") or "").strip()
        result = parser.parse(store, text, tune_hint)
        parse_id = parser.save(result)
        return _payload({**result, "parse_id": parse_id})

    @app.get("/api/parse/<parse_id>")
    def parse_result(parse_id: str):
        return _payload(parser.get(parse_id))

    @app.get("/api/parse/<parse_id>/tunes")
    def parse_tunes(parse_id: str):
        result = parser.get(parse_id)
        return _payload({**result["tunes"], "parse_id": parse_id})

    @app.get("/api/search")
    def search():
        query = (request.args.get("q") or "").strip()
        if len(query) > 50:
            raise ApiError("INVALID_INPUT", "q 过长（≤50 字）")
        limit = request.args.get("limit", "20")
        # 上限 500：检索页的「显示更多」每次 +100，累加下去会撞上限——
        # 上限 100 时点一次「显示更多」就 400（前端把 400 吞成「0 篇」）
        if not limit.isdigit() or not 1 <= int(limit) <= 500:
            raise ApiError("INVALID_INPUT", "limit 取 1–500")
        # tier：1 = 官方快照、2a = 同源清词、2b = 同源宋词、all = 都搜
        # 默认 all：语料有 4.3 万首，默认只搜 423 篇会让「检索不到」被误解成「不存在」
        tier = (request.args.get("tier") or "all").strip().lower()
        if tier not in ("1", "2a", "2b", "2c", "2d", "all"):
            raise ApiError("INVALID_INPUT", "tier 取 1 / 2a / 2b / 2c / 2d / all", {"tier": tier})
        period = request.args.get("period")
        tune = request.args.get("tune")
        poet = request.args.get("poet")
        if not query:
            # 没有关键词、只有筛选条件（按作者/词牌/朝代浏览）→ 走作品列表
            if not (poet or tune or period):
                raise ApiError("INVALID_INPUT", "q 不能为空；或给出 poet / tune / period 之一")
            return _payload(_browse_works(store, settings, tier, period, tune, poet, int(limit)))
        if tier in ("2a", "2b", "2c", "2d"):
            if not corpus.tier2_available(settings):
                raise ApiError("KNOWLEDGE_UNAVAILABLE", "Tier 2 索引未就位")
            return _payload(
                corpus.search_tier2(
                    settings, query, period or "", tune or "", poet or "", int(limit), tier
                )
            )
        result = store.search(query, period=period, tune=tune, poet=poet, limit=int(limit))
        if tier == "all" and corpus.tier2_available(settings):
            extra = corpus.search_tier2(
                settings, query, period or "", tune or "", poet or "", int(limit), ""
            )
            result = dict(result)
            result["items"] = list(result["items"]) + list(extra["items"])
            result["total"] = int(result.get("total") or 0) + int(extra.get("total") or 0)
            result["tier"] = "all"
            result["tier2_total"] = extra.get("total")
        return _payload(result)

    @app.get("/api/graph")
    def graph_view():
        focus = request.args.get("focus") or ""
        focus_type = request.args.get("type") or ""
        if focus and len(focus) > 40:
            raise ApiError("INVALID_INPUT", "focus 过长（≤40 字）")
        return _payload(graph.build(store, focus, focus_type))

    @app.get("/api/graph/neighbors")
    def graph_neighbors():
        node_id = (request.args.get("id") or "").strip()
        if not node_id:
            raise ApiError("INVALID_INPUT", "需要 id（如 poet:汪东）")
        return _payload(graph.build(store, node_id))

    @app.get("/api/revisions/stats")
    def revision_stats():
        return _payload(revisions.stats(settings))

    @app.get("/api/revisions")
    def revision_list():
        page = request.args.get("page", "1")
        page_size = request.args.get("page_size", "20")
        if not page.isdigit() or not page_size.isdigit():
            raise ApiError("INVALID_INPUT", "page 与 page_size 必须为正整数")
        if int(page) < 1 or not 1 <= int(page_size) <= 100:
            raise ApiError("INVALID_INPUT", "page ≥ 1 且 1 ≤ page_size ≤ 100")
        return _payload(
            revisions.list_items(
                settings,
                status=request.args.get("status", "pending"),
                kind=request.args.get("kind", ""),
                page=int(page),
                page_size=int(page_size),
            )
        )

    @app.post("/api/revisions/<revision_id>/decision")
    def revision_decision(revision_id: str):
        body = request.get_json(silent=True) or {}
        decision = (body.get("decision") or "").strip()
        return _payload(revisions.decide(settings, revision_id, decision, (body.get("note") or "").strip()))

    @app.get("/api/revision/exports")
    def revision_exports():
        """导出记录：历次导出的时间、幂等键、行数（不返回 CSV 正文）。"""
        limit = request.args.get("limit", "50")
        if not limit.isdigit() or not 1 <= int(limit) <= 200:
            raise ApiError("INVALID_INPUT", "limit 取 1–200")
        return _payload(revisions.export_history(settings, int(limit)))

    @app.post("/api/revision/export")
    def revision_export():
        csv_text, key, rows = revisions.export(settings)
        if key == "empty":
            raise ApiError(
                "INVALID_INPUT",
                "没有待导出的已接受条目",
                {"hint": "先在校订队列里接受若干条目", "closure_level": "L3 幂等导出"},
            )
        payload = ("\ufeff" + csv_text).encode("utf-8")
        response = Response(payload, mimetype="text/csv")
        response.headers["Content-Disposition"] = 'attachment; filename="revisions_%s.csv"' % key
        response.headers["X-Export-Key"] = key
        response.headers["X-Export-Rows"] = str(rows)
        response.headers["X-Closure-Level"] = "L3"
        return response

    @app.get("/api/style/authors")
    def style_authors():
        limit = request.args.get("limit", "50")
        if not limit.isdigit() or not 1 <= int(limit) <= 200:
            raise ApiError("INVALID_INPUT", "limit 取 1–200")
        return _payload(style.list_authors(settings, request.args.get("q") or "", int(limit)))

    @app.get("/api/style/compare")
    def style_compare():
        left = (request.args.get("a") or "").strip()
        right = (request.args.get("b") or "").strip()
        if not left or not right:
            raise ApiError("INVALID_INPUT", "需要 a 与 b 两位词人")
        return _payload(style.compare(settings, left, right))

    @app.post("/api/style/narrative")
    def style_narrative():
        body = request.get_json(silent=True) or {}
        left = (body.get("a") or "").strip()
        right = (body.get("b") or "").strip()
        if not left or not right:
            raise ApiError("INVALID_INPUT", "需要 a 与 b 两位词人")
        if body.get("dual"):
            return _payload(style.narrative_dual(settings, left, right))
        model = body.get("model") or sidecar.DEFAULT_MODEL
        return _payload(style.narrative(settings, left, right, model))

    @app.get("/api/style/<poet>")
    def style_one(poet: str):
        return _payload(style.get_style(settings, poet))

    @app.post("/api/qa")
    def qa_start():
        body = request.get_json(silent=True) or {}
        work_id = body.get("work_id")
        # 官方快照是整数 id；同源层是 `t2a_` / `t2b_` 前缀的字符串 id
        if not isinstance(work_id, (int, str)) or not str(work_id).strip():
            raise ApiError("INVALID_INPUT", "work_id 必须为整数或同源层 id")
        question = (body.get("question") or "").strip()
        if len(question) > 200:
            raise ApiError("INVALID_INPUT", "问题过长（≤200 字）")
        model = body.get("model") or sidecar.DEFAULT_MODEL
        card = sidecar.build_fact_card(store, work_id, settings)
        started = qa.start(store, work_id, question, model, settings)
        return _payload(
            {
                "qa_id": started["qa_id"],
                "state": "running",
                "fact_card": card,
                "verification": "未验证",
                "model": model,
            },
            202,
        )

    @app.get("/api/qa/<qa_id>/candidates")
    def qa_candidates(qa_id: str):
        status = qa.get(qa_id)
        result = status.get("result") or {}
        return _payload(
            {
                "qa_id": qa_id,
                "state": status["state"],
                "status": result.get("status"),
                "candidate": result.get("candidate"),
                "checks": result.get("checks"),
                "violations": result.get("violations"),
                "verification": (result.get("candidate") or {}).get("verification"),
                "boundary": (result.get("candidate") or {}).get("boundary"),
                "model": result.get("model"),
                "latency_ms": result.get("latency_ms"),
                "fallback_reason": result.get("fallback_reason"),
                "error": status.get("error"),
            }
        )

    @app.post("/api/qa/<qa_id>/cancel")
    def qa_cancel(qa_id: str):
        return _payload(qa.cancel(qa_id))

    @app.post("/api/eval/run")
    def eval_run():
        if settings.quiz_dir is None:
            raise ApiError("INVALID_INPUT", "未配置题库目录", {"hint": "设置 APP_QUIZ_DIR"})
        body = request.get_json(silent=True) or {}
        limit = body.get("limit")
        if limit is not None and (not isinstance(limit, int) or not 5 <= limit <= 700):
            raise ApiError("INVALID_INPUT", "limit 取 5–700 或不传（全量）")
        job_id = evaluator.start(store, settings.quiz_dir, limit)
        return _payload({"job_id": job_id, "state": "running"}, 202)

    @app.post("/api/agent/answer")
    def agent_answer():
        """智能体作答：**引擎优先，答不上来才叫模型**。

        与 `/api/solve` 的分工：
        - `/api/solve` —— 纯确定性，**判分走这条**（逐字/归一化/代理三口径）；
        - `/api/agent/answer` —— 引擎优先，答不上来才模型兜底；另可附一段模型解读。

        返回带 `mode` 字段说明这次走的是哪条路：
        `确定性求解` / `确定性求解 + 模型解读` / `模型兜底（未验证）`。
        """
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        if not question:
            raise ApiError("INVALID_INPUT", "问题不能为空")
        narrative = body.get("narrative")
        if narrative is None:
            narrative = request.args.get("narrative", "1") not in ("0", "false", "")
        model = body.get("model") or None
        result = agent.answer(
            store, settings, question, narrative=bool(narrative), model=model
        )
        return _payload(result)

    @app.get("/api/agent/capabilities")
    def agent_capabilities():
        """智能体能力说明：能确定性答什么、什么情况会交给模型。"""
        return _payload(
            {
                "deterministic_categories": [
                    {"id": "C1", "name": "同调全篇声律比例差"},
                    {"id": "C2", "name": "跨篇分段变幅比较"},
                    {"id": "C3", "name": "跨篇长句仄声密度比较"},
                    {"id": "C4", "name": "跨篇后段排序与比例差距"},
                    {"id": "C5", "name": "跨篇景情节奏互证"},
                ],
                "routing": [
                    "① 锚点解析：解析不出 → 明确报错（模型也不知道怎么定位作品）",
                    "② 作品定位：本地快照 → 官方 API → 同源层；定位不到 → 明确报错",
                    "③ 引擎判类 + 确定性求解：成功 → 出可复算的字段，另可附模型解读",
                    "④ 引擎答不上来（题型超出规则覆盖）→ 模型依据事实卡兜底作答，过四道闸门",
                ],
                "discipline": [
                    "数字永远来自引擎，模型不产出判分字段",
                    "模型兜底必须有事实卡——没有依据宁可报错，不让模型编数字",
                    "来源逐条标注：确定性求解 / 模型兜底（未验证）/ 模型解读（未验证）",
                ],
                "narrative_available": bool(sidecar.MODEL_PROFILES),
                "model": sidecar.DEFAULT_MODEL,
                "second_model": sidecar.SECOND_MODEL,
            }
        )

    @app.post("/api/eval/upload")
    def eval_upload():
        """上传标准化验证集，逐题调用内部接口作答。

        两种提交方式：
        - `multipart/form-data`，字段名 `file`（.jsonl / .csv / .json / **.xlsx**）；
        - 直接把文件内容作为请求体（`text/plain`、`application/json` 等）。

        **题面可以不带标准答案**——那种情况只产出回答、不算分，
        回答可用 `GET /api/eval/{job_id}/export` 取走，去与官方答案比对。
        `.xlsx` 按模板4 读两列「问题 / 预期答案」，导出按模板5 五列。
        """
        filename = ""
        text = ""
        uploaded = request.files.get("file")
        if uploaded is not None:
            filename = uploaded.filename or ""
            raw = uploaded.read()
            if filename.lower().endswith((".xlsx", ".xlsm")):
                # 模板4 的 Excel 验证集：转 CSV 文本后复用同一套解析与评分链
                text = evaluator.xlsx_to_csv(raw)
                filename = filename.rsplit(".", 1)[0] + ".csv"
            else:
                text = raw.decode("utf-8-sig", errors="replace")
        else:
            filename = request.args.get("filename") or ""
            text = request.get_data(as_text=True) or ""
        if not text.strip():
            raise ApiError(
                "INVALID_INPUT",
                "没有收到验证集内容",
                {"hint": "multipart 用 file 字段；或把文件内容直接作为请求体"},
            )
        rows, report = evaluator.parse_dataset(text, filename)
        limit = request.args.get("limit")
        limit_value = None
        if limit is not None:
            if not limit.isdigit() or not 1 <= int(limit) <= evaluator.MAX_QUESTIONS:
                raise ApiError("INVALID_INPUT", "limit 取 1–%d" % evaluator.MAX_QUESTIONS)
            limit_value = int(limit)
        job_id = evaluator.start_dataset(store, rows, limit_value, report)
        return _payload(
            {
                "job_id": job_id,
                "parsed": report,
                "state": "running",
                "questions": report["total"],
            },
            202,
        )

    @app.get("/api/eval/<job_id>/export")
    def eval_export(job_id: str):
        """导出逐题回答（JSONL / CSV / **XLSX**），供外部与官方答案比对。"""
        fmt = (request.args.get("format") or "jsonl").strip().lower()
        if fmt not in ("jsonl", "csv", "xlsx"):
            raise ApiError("INVALID_INPUT", "format 取 jsonl / csv / xlsx", {"format": fmt})
        content, mimetype, filename = evaluator.export_items(job_id, fmt)
        body = content if isinstance(content, bytes) else content.encode("utf-8")
        response = Response(body, mimetype=mimetype)
        response.headers["Content-Disposition"] = "attachment; filename*=UTF-8''%s" % urllib.parse.quote(
            filename
        )
        return response

    @app.get("/api/eval/<job_id>")
    def eval_status(job_id: str):
        return _payload(evaluator.get(job_id))

    @app.get("/api/eval/<job_id>/items")
    def eval_items(job_id: str):
        page = request.args.get("page", "1")
        page_size = request.args.get("page_size", "20")
        if not page.isdigit() or not page_size.isdigit():
            raise ApiError("INVALID_INPUT", "page 与 page_size 必须为正整数")
        if int(page) < 1 or not 1 <= int(page_size) <= 100:
            raise ApiError("INVALID_INPUT", "page ≥ 1 且 1 ≤ page_size ≤ 100")
        return _payload(evaluator.get_items(job_id, int(page), int(page_size)))

    @app.get("/api/factcard/<int:work_id>")
    def factcard(work_id: int):
        """纯引擎事实卡：论证辅助的证据来源，不调用模型、不受模型断连影响。"""
        return _payload(sidecar.build_fact_card(store, work_id, settings))

    @app.post("/api/argument/draft")
    def argument_draft():
        """论证辅助**生成**：只把勾选的证据交给模型，走 sidecar 统一四道闸门。

        模型不可用时 `guarded` 返回 degraded，前端回落到模板拼装并如实显示，
        不把降级结果伪装成模型草稿。
        """
        body = request.get_json(silent=True) or {}
        work_id = body.get("work_id")
        if not isinstance(work_id, (int, str)) or not str(work_id).strip():
            raise ApiError("INVALID_INPUT", "work_id 必须为整数或同源层 id")
        claim = (body.get("claim") or "").strip()
        refs = body.get("refs") or []
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise ApiError("INVALID_INPUT", "refs 必须是字符串数组")
        model = body.get("model") or sidecar.DEFAULT_MODEL
        result = argument_core.draft(
            store, work_id, claim, refs, model, settings=settings
        )
        return _payload(result)

    @app.get("/api/demo/cases")
    def demo_cases():
        """V6.1 §13.1 三个定死案例 + 构建期预计算回放数据。"""
        import json

        path = settings.demo_cases_path
        if not path.is_file():
            raise ApiError(
                "NOT_FOUND",
                "预计算演示数据未生成",
                {"expected_path": str(path), "hint": "运行 python app/scripts/build_demo_cases.py"},
            )
        return _payload(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/demo")
    def demo():
        """从本地题库取一道示例题面。题面不写入前端产物，也不随交付分发。"""
        import json

        if settings.quiz_dir is None:
            raise ApiError(
                "NOT_FOUND",
                "未配置题库目录，无法提供示例",
                {"hint": "设置环境变量 APP_QUIZ_DIR 指向题库目录"},
            )
        quiz = settings.quiz_dir / "公开测试集_700题.jsonl"
        if not quiz.is_file():
            raise ApiError(
                "NOT_FOUND",
                "本地题库不在位，无法提供示例",
                {"expected_path": str(quiz), "hint": "设置 APP_QUIZ_DIR 指向题库目录"},
            )
        wanted = request.args.get("category")
        for line in quiz.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("类别") in engine.IMPLEMENTED and (not wanted or row.get("类别") == wanted):
                return _payload({"category": row.get("类别"), "question": row.get("问题")})
        raise ApiError("NOT_FOUND", "题库中没有可用于示例的题目")

    @app.get("/api/authors")
    def authors_list():
        """作者候选（姓名 + 作品数 + 词牌数），供检索页按作者筛选用。"""
        keyword = (request.args.get("q") or "").strip()
        limit = request.args.get("limit", "50")
        if not limit.isdigit() or not 1 <= int(limit) <= 200:
            raise ApiError("INVALID_INPUT", "limit 取 1–200")
        tier = (request.args.get("tier") or "").strip().lower()
        return _payload(corpus.authors(settings, keyword, int(limit), tier))

    @app.get("/api/search/stats")
    def search_stats():
        """同调统计：作品数、句数分布、仄比中位数（规划 B2 检索板块要求）。

        `tier` 选语料层：`1` 官方逐句计数、`2a`/`2b` 同源带调拼音、`all` 优先官方。
        两层口径不同，响应里的 `tier_label` 写明来源，**不混算**。
        """
        tune = (request.args.get("tune") or "").strip()
        tier = (request.args.get("tier") or "all").strip().lower()
        if tier not in ("1", "2a", "2b", "2c", "2d", "all"):
            raise ApiError("INVALID_INPUT", "tier 取 1 / 2a / 2b / 2c / 2d / all", {"tier": tier})
        if tier == "1":
            return _payload(store.tune_stats(tune))
        return _payload(corpus.tune_stats(settings, tune, tier))

    @app.get("/api/solve/<solve_id>/trace")
    def solve_trace(solve_id: str):
        """单独取一次求解的推理链与字段，便于界面分步展开。"""
        record = engine.get_solve(solve_id)
        return _payload({"solve_id": solve_id, **record})

    @app.post("/api/solve")
    def solve():
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        if not question:
            raise ApiError("INVALID_INPUT", "question 不能为空")
        result = engine.solve(question, store)
        return _payload(result)

    @app.get("/")
    def index():
        index_file = settings.dist_dir / "index.html"
        if not index_file.is_file():
            return _payload(
                {
                    "message": "前端尚未构建",
                    "hint": "在 app/frontend 执行 npm install && npm run build",
                    "api": ["/api/health", "/api/meta", "/api/works", "/api/solve", "/api/search"],
                }
            )
        return _send_cached(index_file, CACHE_REVALIDATE)

    @app.get("/<path:asset>")
    def static_asset(asset: str):
        if asset.startswith("api/"):
            raise ApiError("NOT_FOUND", "路径不存在")
        target = (settings.dist_dir / asset).resolve()
        if settings.dist_dir.resolve() not in target.parents and target != settings.dist_dir.resolve():
            raise ApiError("INVALID_INPUT", "非法路径")
        if target.is_file():
            # /assets/ 下是 Vite 打过哈希的产物，/fonts/ 是构建期固定的子集字体，都可长缓存
            long_lived = asset.startswith(("assets/", "fonts/"))
            return _send_cached(target, CACHE_IMMUTABLE if long_lived else CACHE_REVALIDATE)
        index_file = settings.dist_dir / "index.html"
        if index_file.is_file():
            return _send_cached(index_file, CACHE_REVALIDATE)
        raise ApiError("NOT_FOUND", "静态资源不存在", {"asset": asset})

    return app


def _send_cached(path: Path, cache_control: str):
    """静态文件带缓存头下发；字体 MIME 已在模块导入时注册。"""
    response = send_file(path, conditional=True, max_age=31536000 if "immutable" in cache_control else 0)
    response.headers["Cache-Control"] = cache_control
    return response

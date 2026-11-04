"""官方知识库 API 客户端（可选启用）。

用途：**三层兜底里的第二层**。题面锚点定位到的作品不在本地 Tier 1 快照（423 篇）里时，
按需向官方 API 查询——官方在评测前发布了新作品，这层就能答上。

不启用也完全能用：本地快照 + 同源层（Tier 2a）已经覆盖绝大多数题面。
默认**关闭**，因为运行时零外网是本项目的硬约束（ADR-0004）；
要用就设环境变量：

    APP_QINGCI_KEY=qck_live_xxx      # 团队密钥（不进代码、不进交付包）
    APP_QINGCI_BASE=https://qingci.zhiruitech.com.cn   # 可选，默认官方地址

**密钥只从环境变量读**，绝不写进代码、配置文件或日志。

速率：官方默认每分钟 120 次。逐题查询会超限，所以查到的作品**落本地缓存**
（`app/data/api_cache.sqlite3`），第二次直接命中缓存。
"""

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .errors import ApiError

DEFAULT_BASE = "https://qingci.zhiruitech.com.cn"
API_PREFIX = "/api/v1/knowledge"
TIMEOUT = 20
MAX_CACHE_ROWS = 5000

_LOCK = threading.Lock()
_CACHE: dict = {}
_SEARCH_CACHE: dict = {}


def api_key() -> str:
    return (os.environ.get("APP_QINGCI_KEY") or "").strip()


def base_url() -> str:
    return (os.environ.get("APP_QINGCI_BASE") or DEFAULT_BASE).strip().rstrip("/")


def enabled() -> bool:
    """是否启用了官方 API 兜底。"""
    return bool(api_key())


def cache_path() -> Path:
    raw = os.environ.get("APP_QINGCI_CACHE")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent.parent / "data" / "api_cache.sqlite3"


def _cache_connect() -> sqlite3.Connection:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS works(
            id INTEGER PRIMARY KEY,
            payload TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
        """
    )
    return connection


def cache_get(work_id) -> dict | None:
    with _LOCK:
        if work_id in _CACHE:
            return _CACHE[work_id]
    path = cache_path()
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(path)
        try:
            row = connection.execute(
                "SELECT payload FROM works WHERE id = ?", (int(work_id),)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        payload = json.loads(row[0])
    except ValueError:
        return None
    with _LOCK:
        _CACHE[work_id] = payload
    return payload


def cache_put(work_id: int, payload: dict) -> None:
    with _LOCK:
        _CACHE[work_id] = payload
    try:
        connection = _cache_connect()
        try:
            connection.execute(
                "INSERT OR REPLACE INTO works(id, payload, fetched_at) VALUES (?,?,?)",
                (int(work_id), json.dumps(payload, ensure_ascii=False), time.time()),
            )
            connection.execute(
                "DELETE FROM works WHERE id NOT IN "
                "(SELECT id FROM works ORDER BY fetched_at DESC LIMIT ?)",
                (MAX_CACHE_ROWS,),
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error:
        pass  # 缓存写不进去不影响作答


def _request(path: str, params: dict | None = None) -> dict:
    key = api_key()
    if not key:
        raise ApiError("KNOWLEDGE_UNAVAILABLE", "未配置官方 API 密钥（APP_QINGCI_KEY）")
    url = base_url() + API_PREFIX + path
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if clean:
            url += "?" + urllib.parse.urlencode(clean, encoding="utf-8")
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        raise ApiError(
            "KNOWLEDGE_UNAVAILABLE",
            "官方知识库返回 %d" % exc.code,
            {"status": exc.code, "body": body},
        )
    except urllib.error.URLError as exc:
        raise ApiError("KNOWLEDGE_UNAVAILABLE", "连不上官方知识库", {"reason": str(exc)})
    except (ValueError, OSError) as exc:
        raise ApiError("KNOWLEDGE_UNAVAILABLE", "官方知识库响应无法解析", {"reason": str(exc)})
    if isinstance(payload, dict) and payload.get("error"):
        raise ApiError("KNOWLEDGE_UNAVAILABLE", str(payload["error"]))
    return payload


def list_works(q: str = "", poet: str = "", tune: str = "", page: int = 1, page_size: int = 20) -> dict:
    """词作检索。`q` 是字面子串（最多 200 字符），`poet` 按作者精确筛。

    检索结果**也进缓存**：官方默认 120 次/分钟，逐题检索必然超限，
    而同一批题的检索参数高度重复（同一首句、同一词人），缓存命中率很高。
    """
    cache_key = "%s|%s|%s|%d|%d" % (q, poet, tune, page, page_size)
    with _LOCK:
        hit = _SEARCH_CACHE.get(cache_key)
    if hit is not None:
        return hit
    payload = _request(
        "/works",
        {"q": q[:200], "poet": poet, "tune_pattern": tune, "page": page, "page_size": page_size},
    )
    with _LOCK:
        if len(_SEARCH_CACHE) > 2000:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[cache_key] = payload
    return payload


def work_detail(work_id) -> dict:
    """作品详情，含 `prosody`（逐句 line_no/text/character_count/ping_count/ze_count）。"""
    cached = cache_get(work_id)
    if cached:
        return cached
    payload = _request("/works/%s" % work_id)
    if isinstance(payload, dict) and payload.get("id") is not None:
        cache_put(payload["id"], payload)
    return payload


def _cjk(text: str) -> str:
    return "".join(ch for ch in (text or "") if "一" <= ch <= "鿿")


def resolve_work(label: str, poet: str, tune: str, first: str) -> dict | None:
    """按题面锚点在官方 API 里定位作品，返回**与本地快照同构**的作品对象。

    定位口径与本地 `store.resolve()` 一致：**首句 + 词人 + 词牌必须唯一命中**，
    命中多条或零条都返回 None（不猜、不取第一条）。

    检索用的是**原文连续片段**（含标点）：官方 `q` 是字面子串检索，
    去标点后再拼起来会与原文不连续，检索不到。
    """
    if not enabled():
        return None
    key = _cjk(first)
    if not key:
        return None
    raw = (first or "").strip()
    probes = []
    for size in (4, 6, 8, 12):
        if len(raw) >= size:
            probe = raw[:size]
            if probe not in probes:
                probes.append(probe)
    if not probes:
        return None

    candidates: dict = {}
    for probe in probes:
        try:
            listing = list_works(q=probe, poet=poet, page=1, page_size=50)
        except ApiError:
            continue
        for item in listing.get("items") or []:
            if item.get("id") is not None and _matches_anchor(item, poet, tune, key):
                candidates[item["id"]] = item
    if len(candidates) != 1:
        return None
    try:
        return work_detail(next(iter(candidates)))
    except ApiError:
        return None


def _matches_anchor(item: dict, poet: str, tune: str, first_key: str) -> bool:
    """候选作品是否与题面锚点一致（首句包含 + 词人 + 词牌）。"""
    poet_name = (item.get("poet") or {}).get("name") if isinstance(item.get("poet"), dict) else item.get("poet")
    if poet and (poet_name or "") != poet:
        return False
    item_tune = item.get("tune_pattern") or item.get("tune") or ""
    if tune and item_tune != tune:
        return False
    text = item.get("text") or ""
    return first_key in _cjk(text)

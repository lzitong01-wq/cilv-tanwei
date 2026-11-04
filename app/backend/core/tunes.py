"""词调身份层（分册 A §A4）：把「调名 / 词调 / 体式」分开管理。

模型（A4.1）：
    tune(词调) 1──* tune_alias(调名/异名)
    tune 1──* form(体式：字数序列/分阕)
    form *──* work(作品)
    form *──* source(来源与版本)

门禁（A4.4）：
- 423 篇上 100% 命中唯一词调（可多体，不可多调）；
- 别名归并有 ≥50 条抽样复核记录；
- 身份层不引入外部词谱文件，只记录来源与允许的派生事实。

业务键按 A4.2：`词调名 | 作者 | 总字数`；同键多条时展示名加 `-1/-2` 后缀。
内部唯一 ID 取内容哈希，**不随排序变化**（图谱与证据链可直接引用）。
"""

import hashlib
import threading
from collections import defaultdict

from . import engine
from . import tune_norm

BUILD_LOCK = threading.Lock()
_CACHE: dict = {}


def _stable_id(prefix: str, *parts) -> str:
    payload = "|".join(str(part) for part in parts)
    return "%s_%s" % (prefix, hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12])


def _total_chars(work: dict) -> int:
    _, lines = engine._prosody_lines(work)
    return sum(line["character_count"] for line in lines)


def _pattern(work: dict) -> tuple[int, ...]:
    _, lines = engine._prosody_lines(work)
    return tuple(line["character_count"] for line in lines)


def _corpus_layer(settings) -> dict:
    """同源层的词调身份（清词 + 宋词，43,191 首）。

    与 Tier 1 的差别只有一处：**同源层没有分阕信息**，所以体式只按字数序列判定。
    官方层（A4.4 门禁）的指标仍只按 Tier 1 的 423 篇算，不受这里影响——
    两层的统计分开记，不混算（V6.1 ADR-0002）。
    """
    from . import corpus as corpus_core

    if not corpus_core.tier2_available(settings):
        return {"available": False, "tunes": [], "works": 0, "forms": 0, "layers": []}

    connection = corpus_core._connect(settings)
    try:
        rows = connection.execute(
            "SELECT id, title, poet, dynasty, tune, pattern, line_count FROM works"
        ).fetchall()
    finally:
        connection.close()

    tunes: dict[str, dict] = defaultdict(
        lambda: {"name": "", "works": 0, "poets": set(), "periods": defaultdict(int), "forms": {}}
    )
    for row in rows:
        name = (row["tune"] or "").strip()
        if not name:
            continue
        # 题记体作品（作者只留题记、没给词牌）的调名是整句题记——
        # 那不是词牌，建出来的体式统计也没有意义，不计入身份层
        if tune_norm.is_caption_like(name):
            continue
        entry = tunes[name]
        entry["name"] = name
        entry["works"] += 1
        if row["poet"]:
            entry["poets"].add(row["poet"])
        entry["periods"][row["dynasty"] or "未标注"] += 1
        pattern = tuple(
            int(part) for part in (row["pattern"] or "").split(",") if part.isdigit()
        )
        if not pattern:
            continue
        # 体式按**句长序列**判定（同源层无分阕）
        form_id = _stable_id("cform", name, list(pattern))
        entry["forms"].setdefault(
            form_id,
            {
                "form_id": form_id,
                "split_after": None,
                "pattern": list(pattern),
                "total_chars": sum(pattern),
                "work_ids": [],
            },
        )["work_ids"].append(row["id"])

    out = []
    for name, entry in tunes.items():
        forms = []
        for form in entry["forms"].values():
            forms.append(
                {
                    **form,
                    "business_key": "%s|同源|%d" % (name, form["total_chars"]),
                    "tune_id": "tune_" + name,
                    "source": "同源带调拼音语料（句长序列派生）",
                    "tier_label": "同源层 · 句式统计（非官方口径）",
                    "display": "%s#%d句" % (name, len(form["pattern"])),
                }
            )
        out.append(
            {
                "tune_id": "tune_" + name,
                "name": name,
                "works": entry["works"],
                "poets": sorted(entry["poets"]),
                "periods": dict(entry["periods"]),
                "forms": forms,
            }
        )
    out.sort(key=lambda item: (-item["works"], item["name"]))
    layers = defaultdict(int)
    for row in rows:
        layers[row["dynasty"] or "未标注"] += 1
    return {
        "available": True,
        "tunes": out,
        "works": len(rows),
        "forms": sum(len(item["forms"]) for item in out),
        "layers": [{"period": key, "works": value} for key, value in sorted(layers.items())],
        "boundary": (
            "同源层体式只按字数序列判定（无分阕信息），与官方层的体式口径不同，"
            "两层分开记、不合并统计。"
        ),
    }


def corpus_summary(store, settings, top: int = 60) -> dict:
    """同源层摘要：规模统计 + 最热门的前 N 个词调 + 词牌名清单。

    同源层有近 2,000 个词调、数千个体式，**全量返回是几兆的 JSON**——
    概览只给规模与热门榜，某个词调的详情走 `/api/tunes/{tune_id}` 单独取。

    但**词牌名清单**（1,898 个短字符串，约 20 KB）要全给：
    检索页的「按词牌筛选」下拉得能选到同源层的调，
    否则「壶中天」这类只在同源层出现的调就只能手输。
    """
    with BUILD_LOCK:
        if "corpus" not in _CACHE:
            _CACHE["corpus"] = _corpus_layer(settings)
    layer = _CACHE["corpus"]
    return {
        "available": layer["available"],
        "works": layer.get("works", 0),
        "tunes": len(layer.get("tunes") or []),
        "forms": layer.get("forms", 0),
        "layers": layer.get("layers") or [],
        "top": [
            # 概览不需要体式详情（每个体式都带全部 work_ids）——
            # 60 个词调带上 forms 是 1.3 MB，精简后约 100 KB
            {
                "tune_id": item["tune_id"],
                "name": item["name"],
                "works": item["works"],
                "poets": item["poets"],
                "periods": item["periods"],
            }
            for item in (layer.get("tunes") or [])[:top]
        ],
        "names": [item["name"] for item in (layer.get("tunes") or [])],
        "all": [
            {"name": item["name"], "works": item["works"]}
            for item in (layer.get("tunes") or [])
        ],
        "boundary": layer.get("boundary", ""),
    }


def corpus_tune(store, settings, name: str) -> dict | None:
    """按词牌名取同源层的词调详情（含它的全部体式）。"""
    with BUILD_LOCK:
        if "corpus" not in _CACHE:
            _CACHE["corpus"] = _corpus_layer(settings)
    for item in _CACHE["corpus"].get("tunes") or []:
        if item["name"] == name:
            return {**item, "boundary": _CACHE["corpus"].get("boundary", "")}
    return None


def build(store, settings=None) -> dict:
    """从 Tier 1 快照反推身份层；423 篇规模下每次构建都在毫秒级。

    `settings` 给了就把**同源层**（清词 + 宋词）也算进来——词调总数从 100 变成
    1,897 个，界面上的「词调身份层」才看得出语料的真实规模。
    官方层（A4.4 门禁）的指标仍然只按 Tier 1 的 423 篇算。

    两层**分开缓存**：官方层是毫秒级的小构建，同源层要扫 4.3 万首，
    分开缓存才能让「只要官方层」的调用不被同源层拖慢。
    """
    with BUILD_LOCK:
        if "official" not in _CACHE:
            _CACHE["official"] = _build_official(store)
        result = _CACHE["official"]
        if settings is not None:
            if "corpus" not in _CACHE:
                _CACHE["corpus"] = _corpus_layer(settings)
            result = {**result, "corpus": _CACHE["corpus"]}
        return result


def _build_official(store) -> dict:
    """官方层（Tier 1 快照）身份层。"""
    tunes: dict[str, dict] = {}
    forms: dict[str, list[dict]] = defaultdict(list)
    aliases: list[dict] = []
    missing = []

    for work in store.all():
        tune_name = (work.get("tune_pattern") or "").strip()
        work_id = int(work["id"])
        if not tune_name:
            missing.append(work_id)
            continue
        title = work.get("title") or ""
        poet = (work.get("poet") or {}).get("name", "")
        tune = tunes.setdefault(
            tune_name,
            {
                "tune_id": "tune_" + tune_name,
                "name": tune_name,
                "works": 0,
                "forms": [],
                "periods": defaultdict(int),
                "poets": set(),
            },
        )
        tune["works"] += 1
        tune["periods"][work.get("period_label") or "未标注"] += 1
        if poet:
            tune["poets"].add(poet)

        prosody, _lines = engine._prosody_lines(work)
        prosody_split = prosody.get("split_after_line")
        pattern = _pattern(work)
        total = _total_chars(work)
        # 体式由**句式本身**决定（字数序列 + 分阕），与作者无关：
        # 同一词调下相同句式的作品属于同一体式，作者只用于展示名消歧。
        form_id = _stable_id("form", tune_name, list(pattern), prosody_split)
        form = next((item for item in forms[tune_name] if item["form_id"] == form_id), None)
        if form is None:
            # 业务键 = 词调名|作者|总字数；同键同体式才归并为同一 form
            form = {
                "form_id": form_id,
                "split_after": prosody_split,
                "business_key": "%s|%s|%d" % (tune_name, poet, total),
                "tune_id": tune["tune_id"],
                "pattern": list(pattern),
                "total_chars": total,
                "work_ids": [],
                "source": "Tier 1 官方快照（句式派生）",
                "tier_label": "官方标注 · 句式统计",
            }
            forms[tune_name].append(form)
        form["work_ids"].append(work_id)

        # 别名：完整题名是作品的展示名，去掉后缀得到主调名用于归并
        display = title or tune_name
        alias_record = {
            "alias": display,
            "tune_id": tune["tune_id"],
            "tune": tune_name,
            "work_id": work_id,
            "poet": poet,
            "merged": True,
            "rule": "取「·」前的主调名与官方 tune_pattern 比对一致" if display != tune_name else "题名与词牌一致",
        }
        if display != tune_name and display.split("·", 1)[0].strip() != tune_name:
            alias_record["merged"] = False
            alias_record["rule"] = "前缀与 tune_pattern 不一致，待人工复核，不强行归并"
        aliases.append(alias_record)

    for tune in tunes.values():
        tune["forms"] = forms[tune["name"]]
        tune["periods"] = dict(tune["periods"])
        tune["poets"] = sorted(tune["poets"])
        # 展示名后缀：同一业务键（词调|作者|总字数）对应多条体式时加 -1/-2
        key_count: dict[str, int] = defaultdict(int)
        for form in tune["forms"]:
            key_count[form["business_key"]] += 1
        seen: dict[str, int] = defaultdict(int)
        for form in tune["forms"]:
            seen[form["business_key"]] += 1
            total_forms = key_count[form["business_key"]]
            suffix = "-%d" % seen[form["business_key"]] if total_forms > 1 else ""
            form["display"] = "%s#%d句%s" % (tune["name"], len(form["pattern"]), suffix)

    result = {
        "tunes": sorted(tunes.values(), key=lambda item: -item["works"]),
        "aliases": aliases,
        "stats": {
            "works": len(store.all()),
            "tunes": len(tunes),
            "forms": sum(len(forms) for forms in forms.values()),
            "multi_form_tunes": sum(1 for tune in tunes.values() if len(tune["forms"]) > 1),
            "aliases": len(aliases),
            "unmerged_aliases": sum(1 for record in aliases if not record["merged"]),
            "missing_tune": missing,
            "unique_tune_rate": round(
                (len(store.all()) - len(missing)) / len(store.all()), 4
            ),
        },
        "boundary": (
            "身份层只记录官方快照派生的句式事实；不引入外部词谱文件。"
            "体式是「库内已见句式」的归并，不是词谱校勘；同调异体并存，不强行定体。"
        ),
    }
    return result


def get(store, tune_id: str) -> dict:
    data = build(store)
    for tune in data["tunes"]:
        if tune["tune_id"] == tune_id:
            return {**tune, "boundary": data["boundary"]}
    from .errors import ApiError

    raise ApiError("NOT_FOUND", "词调不在身份层中", {"tune_id": tune_id})


def resolve(store, alias: str, settings=None) -> dict:
    """别名 → 词调。命中多条时全部返回，不静默取第一条。

    官方层（423 篇 / 100 个词调）之外，同源层还有 1,897 个词调——
    「雨霖铃」这类常见调**只在同源层**。官方层没命中时自动回落到同源层，
    并在 `source` 里写明来源，避免「查不到」被误解成「不存在」。
    """
    data = build(store)
    keyword = (alias or "").strip()
    if not keyword:
        return {"alias": keyword, "matches": [], "boundary": "空查询"}
    hits = [record for record in data["aliases"] if keyword in record["alias"]]
    source = "官方标注"
    if not hits and settings is not None:
        corpus_item = corpus_tune(store, settings, keyword)
        if corpus_item:
            hits = [
                {
                    "alias": keyword,
                    "tune_id": corpus_item["tune_id"],
                    "tune": corpus_item["name"],
                    "work_id": None,
                    "poet": "",
                    "merged": True,
                    "rule": "官方层没有这个调，改在同源层命中",
                    "works": corpus_item.get("works", 0),
                    "poets": corpus_item.get("poets", []),
                    "tier_label": corpus_item.get(
                        "tier_label", "同源带调拼音（非官方口径）"
                    ),
                }
            ]
            source = "同源带调拼音（非官方口径）"
    return {
        "alias": keyword,
        "matches": hits[:20],
        "total": len(hits),
        "source": source,
        "boundary": (
            "同调异名、同名异调都可能存在；命中多条时全部列出，"
            "不静默取第一条。归并不等价的项已标待人工复核。"
            + (
                "官方层没有的词调已回落到同源层命中，来源见 source。"
                if source != "官方标注"
                else ""
            )
        ),
    }

def review_sample(store, size: int = 50) -> dict:
    """别名归并的抽样复核记录（A4.4 门禁）。种子固定，结果可复现。"""
    import random

    data = build(store)
    aliases = data["aliases"]
    rng = random.Random(20261104)
    picked = list(aliases)
    rng.shuffle(picked)
    picked = picked[: min(size, len(picked))]
    return {
        "size": len(picked),
        "seed": 20261104,
        "merged": sum(1 for record in picked if record["merged"]),
        "unmerged": [record for record in picked if not record["merged"]],
        "items": picked,
        "note": "抽样复核只记录归并依据与待复核项，不声称别名等同调。",
    }

"""多音字修正表（分册 A §A5）：从真实差异归纳「字 + 上下文 → 读音」规则。

**禁止逐字硬编码**（A5.2）：规则必须带上下文，不允许只凭"这个字"就改读音。
规则来源是校订队列里"本地首读与官方计数不一致"的句子，逐条解出
「把哪个字改成什么读音才能与官方计数一致」，再按上下文聚合。
同一上下文出现多个目标读音时记为**冲突**，不强行给规则。
"""

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from . import chars as char_core
from . import engine
from . import prosody as prosody_core
from .errors import ApiError


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % Path(db_path).as_posix(), uri=True)


def _tone_of(token: str) -> int:
    tail = token[-1] if token else ""
    return int(tail) if tail.isdigit() else 0


def _ping_ze(token: str) -> str:
    tone = _tone_of(token)
    if tone in (1, 2):
        return "平"
    if tone in (3, 4):
        return "仄"
    return "未知"


def _context(items: list[dict], index: int) -> dict:
    """上下文取相邻字；句首句尾用边界符，避免不同位置被错误合并。"""
    prev_char = items[index - 1]["char"] if index > 0 else "^"
    next_char = items[index + 1]["char"] if index + 1 < len(items) else "$"
    return {"prev": prev_char, "next": next_char}


def solve_line(line_text: str, candidates: list[str], local_ping: int, api_ping: int,
               guxin: dict | None = None, prosody_series: str | None = None) -> list[dict]:
    """解出「改哪几个字、改成什么读音」能让本地计数等于官方计数。

    返回若干条 {char, reading, prev, next, index}；解不出来返回 []。
    只允许改动候选多音字，且只接受**最少改动数**的解。

    guxin 是「字 → 平/仄」的外部字表层（古音平仄）。当某字古音平仄与今音不同，
    且改判方向与目标一致时，它也是一条合法改判——这是官方计数之外的**第二个证据源**
    （分册 A §A2：官方 prosody > 词家自注 > 外部字表 > 本地字典）。

    prosody_series 是该句按**词谱体式**对齐后的逐字平仄串（"平"/"仄"/"中"）。
    它只在体式唯一或高分时才会给出（§A3.2 末句），是**第三个证据源**，
    优先级高于古音层：词谱给的是格律本身，古音只是单字读法。
    """
    items = char_core.annotate(line_text)
    if not items:
        return []
    delta = api_ping - local_ping
    if delta == 0:
        return []

    targets = []
    for index, item in enumerate(items):
        if item["char"] not in candidates:
            continue
        for reading in item["candidates"]:
            new_ping_ze = _ping_ze(reading)
            if new_ping_ze == "未知" or new_ping_ze == item["ping_ze"]:
                continue
            # 只允许朝目标方向改：缺平就把仄改平，缺仄就把平改仄
            if delta > 0 and item["ping_ze"] == "仄" and new_ping_ze == "平":
                targets.append({"index": index, "char": item["char"], "reading": reading, "sign": 1})
            elif delta < 0 and item["ping_ze"] == "平" and new_ping_ze == "仄":
                targets.append({"index": index, "char": item["char"], "reading": reading, "sign": -1})

    # 词谱体式证据：谱上这一位置该读什么平仄，最接近官方口径
    if prosody_series:
        for index, item in enumerate(items):
            if index >= len(prosody_series):
                break
            spectrum = prosody_series[index]
            if spectrum not in ("平", "仄") or spectrum == item["ping_ze"]:
                continue
            sign = 1 if spectrum == "平" else -1
            if sign != (1 if delta > 0 else -1):
                continue
            targets.append(
                {
                    "index": index,
                    "char": item["char"],
                    "reading": "谱" + spectrum,
                    "sign": sign,
                    "source": "词谱体式",
                }
            )

    # 古音平仄证据：今音读出来与官方不一致时，古音层是另一条可解释的读法
    if guxin:
        for index, item in enumerate(items):
            ancient = guxin.get(item["char"])
            if not ancient or ancient == item["ping_ze"]:
                continue
            sign = 1 if ancient == "平" else -1
            if sign != (1 if delta > 0 else -1):
                continue
            targets.append(
                {
                    "index": index,
                    "char": item["char"],
                    "reading": "古" + ancient,
                    "sign": sign,
                    "source": "古音平仄",
                }
            )

    if not targets:
        return []

    # 单字解优先
    for target in targets:
        if target["sign"] == (1 if delta > 0 else -1) and abs(delta) == 1:
            return [target]
    # 两字同向组合
    if abs(delta) == 2:
        for first in targets:
            for second in targets:
                if second["index"] <= first["index"]:
                    continue
                if first["sign"] == second["sign"] == (1 if delta > 0 else -1):
                    return [first, second]
    # 贪心多字组合：按位置依次取同向候选，累加到 |delta|
    wanted = 1 if delta > 0 else -1
    picked = []
    accumulated = 0
    for target in sorted(targets, key=lambda item: item["index"]):
        if target["sign"] != wanted:
            continue
        picked.append(target)
        accumulated += target["sign"]
        if accumulated == delta:
            return picked
    return []


def build_rules(db_path: Path, snapshot_path: Path, guxin: dict | None = None,
                use_prosody: bool = True) -> dict:
    """从校订队列归纳规则；返回规则、冲突与覆盖率指标。

    guxin：字 → 平/仄 的外部字表层（古音平仄），可为空。
    use_prosody：是否启用词谱体式证据（需要已生成词谱索引）。
    """
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT work_id, line_no, line_text, local_ping, api_ping, candidates FROM revisions"
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[tuple, Counter] = defaultdict(Counter)
    sources: dict[tuple, str] = {}
    solved_lines = 0
    unsolved_lines = []
    # 词谱逐字平仄：按作品算一次候选体式并对齐到各句
    series_by_line: dict[int, str] = {}
    prosody_used = 0
    if use_prosody and prosody_core.available():
        works = {}
        with snapshot_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                works[int(record["id"])] = record
        for work_id, work in works.items():
            try:
                _prosody, plines = engine._prosody_lines(work)
            except ApiError:
                continue
            sentences = [line["character_count"] for line in plines]
            result = prosody_core.best(
                work.get("tune_pattern") or "",
                sentences,
                (work.get("prosody") or {}).get("split_after_line"),
            )
            if result is None or not result["allowed_for_alignment"]:
                continue
            aligned = prosody_core.align(result["form"], sentences)
            if aligned is None:
                continue
            prosody_used += 1
            for position, line in enumerate(plines):
                if position < len(aligned):
                    series_by_line[int(line["line_no"])] = aligned[position]

    for work_id, line_no, line_text, local_ping, api_ping, raw_candidates in rows:
        candidates = json.loads(raw_candidates or "[]")
        solutions = solve_line(
            line_text, candidates, local_ping, api_ping, guxin, series_by_line.get(int(line_no))
        )
        if not solutions:
            unsolved_lines.append({"work_id": work_id, "line_no": line_no})
            continue
        solved_lines += 1
        for solution in solutions:
            items = char_core.annotate(line_text)
            context = _context(items, solution["index"])
            key = (solution["char"], context["prev"], context["next"])
            grouped[key][solution["reading"]] += 1
            sources[key] = solution.get("source", "多音字改判")

    rules = []
    conflicts = []
    for (char, prev, next_char), counter in sorted(grouped.items()):
        if len(counter) > 1:
            conflicts.append(
                {
                    "char": char,
                    "prev": prev,
                    "next": next_char,
                    "options": dict(counter),
                    "note": "同一上下文出现多个目标读音，按无法判定处理，不给规则",
                }
            )
            continue
        reading, count = counter.most_common(1)[0]
        rules.append(
            {
                "char": char,
                "prev": prev,
                "next": next_char,
                "reading": reading,
                "evidence_count": count,
                "status": "active",
                "source": sources.get((char, prev, next_char), "多音字改判"),
                "basis": "由校订队列中本地计数与官方计数的差异归纳",
            }
        )

    # 字级共识规则：某个字在**所有**已解实例中读音完全一致时，才生成一条字级规则。
    # 它不是手写的逐字硬编码——读音来自差异归纳，且只在无歧义时生成；
    # 跨语料泛化时，精确上下文规则先命中，字级共识只作兜底。
    char_votes: dict[str, Counter] = defaultdict(Counter)
    for (char, _prev, _next), counter in grouped.items():
        if len(counter) == 1:
            reading = next(iter(counter))
            char_votes[char][reading] += sum(counter.values())
    char_rules = []
    for char, counter in sorted(char_votes.items()):
        if len(counter) > 1:
            continue
        reading, count = counter.most_common(1)[0]
        char_rules.append(
            {
                "char": char,
                "prev": "*",
                "next": "*",
                "reading": reading,
                "evidence_count": count,
                "status": "fallback",
                "source": "多音字改判",
                "basis": "该字在已解实例中读音完全一致，作为跨语料兜底；精确上下文规则优先",
            }
        )

    total = len(rows)
    return {
        "rules": rules,
        "char_rules": char_rules,
        "conflicts": conflicts,
        "metrics": {
            "diff_lines": total,
            "solved_lines": solved_lines,
            "coverage": round(solved_lines / total, 4) if total else 0.0,
            "unsolved": unsolved_lines,
            "rule_count": len(rules),
            "prosody_aligned_works": prosody_used,
            "conflict_count": len(conflicts),
        },
    }


def apply_rules(line_text: str, rules: list[dict], index: dict | None = None,
                target_ping: int | None = None) -> dict:
    """按上下文规则改写逐字读音。

    **每句最多改一个字**：修正表是从「最小改动解」归纳出来的，若逐字独立应用，
    一句里命中多条规则时会互相抵消（改 +1 又改 -1，净效果为零），反而把句子改坏。
    因此：先收集所有命中字，恰好一个才应用；多个命中说明规则有歧义，
    按「无法判定」不应用（命中的是字级共识兜底时优先用它）。
    """
    items = char_core.annotate(line_text)
    if index is None:
        index = {}
        for rule in rules:
            index.setdefault(rule["char"], []).append(rule)

    matched: list[dict] = []
    for position, item in enumerate(items):
        context = _context(items, position)
        exact = None
        fallback = None
        for rule in index.get(item["char"], []):
            if rule["prev"] == "*" and rule["next"] == "*":
                if fallback is None:
                    fallback = rule
                continue
            if rule["prev"] == context["prev"] and rule["next"] == context["next"]:
                exact = rule
                break
        chosen = exact or fallback
        if chosen is not None:
            matched.append({"position": position, "item": item, "rule": chosen, "exact": exact is not None})

    def _apply(entry: dict) -> dict:
        item = dict(entry["item"])
        rule = entry["rule"]
        reading = rule["reading"]
        if reading.startswith("谱"):
            # 词谱体式只给平仄类，不给具体读音
            item["ping_ze"] = reading[1]
            item["source"] = "prosody_rule"
            item["tier_label"] = "词谱体式（外部字表）"
            return item
        if reading.startswith("古"):
            # 古音平仄只给出平仄类，不给具体读音：保留今音拼音，标注平仄来源
            item["ping_ze"] = reading[1]
            item["source"] = "guxin_rule"
            item["tier_label"] = "古音平仄（外部字表）"
            return item
        item["pinyin"] = reading
        item["tone"] = _tone_of(reading)
        item["tone_name"] = char_core.TONE_NAMES.get(item["tone"], "未知")
        item["ping_ze"] = _ping_ze(reading)
        item["source"] = "polyphone_rule"
        item["tier_label"] = "修正表（数据归纳）"
        return item

    hits = []
    chosen = None
    if target_ping is not None:
        # 有明确目标计数时：选**能让平声总数对上**的那条规则（可能不止一个字命中）。
        # target_ping 是目标**总数**，不是差值——所以直接比 trial_ping。
        for entry in matched:
            trial = list(items)
            trial[entry["position"]] = _apply(entry)
            trial_ping = sum(1 for item in trial if item["ping_ze"] == "平")
            if trial_ping == target_ping:
                chosen = entry
                break
    elif len(matched) == 1:
        # 无目标计数时：只在**唯一命中**时才用，避免过度修正
        chosen = matched[0]

    if chosen is not None:
        items[chosen["position"]] = _apply(chosen)
        hits.append(
            {
                "index": chosen["position"],
                "char": chosen["item"]["char"],
                "reading": chosen["rule"]["reading"],
                "exact": chosen["exact"],
            }
        )
    return {"items": items, "hits": hits, "ambiguous": len(matched) > 1}


def ping_ze_of(text: str, rules: list[dict] | None = None, index: dict | None = None,
               target_ping: int | None = None) -> tuple[int, int]:
    """本地口径平仄计数；给规则则先应用修正表再计数。

    target_ping 给了目标平声数时，选"能让计数对上"的那条规则；
    不给时只在唯一命中时应用（宁可不用，也不猜）。
    """
    if rules:
        result = apply_rules(text, rules, index, target_ping)
        items = result["items"]
    else:
        items = char_core.annotate(text)
    ping = sum(1 for item in items if item["ping_ze"] == "平")
    ze = sum(1 for item in items if item["ping_ze"] == "仄")
    return ping, ze


def strip_diacritics(syllable: str) -> str:
    """同源拼音是带调字母：去调号取纯字母，便于与 pypinyin 的数字调比对。"""
    table = str.maketrans(
        "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ",
        "aaaaeeeeiiiioooouuuuuuuu",
    )
    return (syllable or "").translate(table)

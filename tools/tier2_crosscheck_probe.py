#!/usr/bin/env python3
"""Tier 2 同源交叉验证：poetry-source 清词自带拼音 vs 官方 API 逐句标注。

回答三个问题：
1) 官方 200 篇清词能否在 poetry-source 中唯一定位；
2) 库内正文与官方正文是否逐字同源；
3) 直接采用库内自带拼音计算，得到的逐句平/仄计数与官方标注一致多少。
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from pypinyin import Style, pinyin

CJK = re.compile(r"[㐀-鿿]")
PUNCT = set("，。？！；、：,.?!;:\"'“”‘’()（）《》〈〉…—－-　 ")
ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "知识库审计快照V1.jsonl"


def norm(text):
    return "".join(CJK.findall(text or ""))


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


TONE_MAP = {
    "ā": "ping", "á": "ping", "ǎ": "ze", "à": "ze",
    "ē": "ping", "é": "ping", "ě": "ze", "è": "ze",
    "ī": "ping", "í": "ping", "ǐ": "ze", "ì": "ze",
    "ō": "ping", "ó": "ping", "ǒ": "ze", "ò": "ze",
    "ū": "ping", "ú": "ping", "ǔ": "ze", "ù": "ze",
    "ǖ": "ping", "ǘ": "ping", "ǚ": "ze", "ǜ": "ze",
}


def tone_of(syllable):
    """库内拼音为带调字母，取末尾调号判定平仄；无调号视为轻声未知。"""
    for char in reversed(syllable or ""):
        if char in TONE_MAP:
            return TONE_MAP[char]
    return None


def parse_line_pinyin(text, pinyin_text):
    """把一句的拼音串拆成逐字平/仄；返回 (classes, note)。"""
    chars = CJK.findall(text)
    tokens = [token for token in (pinyin_text or "").split() if token and not set(token) <= PUNCT]
    classes = []
    for token in tokens:
        tone = tone_of(token)
        if tone is None:
            classes.append(None)
        else:
            classes.append(tone)
    if len(classes) != len(chars):
        return None, "音节数%d与汉字数%d不一致" % (len(classes), len(chars))
    return classes, None


def main():
    dynasty = sys.argv[1] if len(sys.argv) > 1 else "清"
    lib_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else (ROOT / ".workbuddy" / "reference" / "tier2" / "qing")
    out_name = sys.argv[3] if len(sys.argv) > 3 else "Tier2同源交叉验证V1.json"
    works = load_jsonl(SNAPSHOT)
    target_works = [w for w in works if (w.get("period_label") or "") == dynasty]

    library = {}
    for path in sorted(lib_dir.glob("ci.%s.*.json" % dynasty)):
        if path.name.endswith(".base.json") or path.name.endswith(".pinyin.json"):
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item.get("id"):
                library.setdefault(item["id"], item)
    # 拼音在独立的 .pinyin.json 分片里，按 id 合并
    pinyin_map = {}
    for path in sorted(lib_dir.glob("ci.%s.*.pinyin.json" % dynasty)):
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item.get("id") and item.get("pinyin"):
                pinyin_map.setdefault(item["id"], item["pinyin"])
    for item_id, item in library.items():
        if item_id in pinyin_map:
            item["pinyin"] = pinyin_map[item_id]
    print("%s词库作品数" % dynasty, len(library), "带拼音", len(pinyin_map))

    by_first = {}
    for item in library.values():
        content = item.get("content") or []
        if not content:
            continue
        key = norm(content[0])[:8]
        if key:
            by_first.setdefault(key, []).append(item)

    stats = Counter()
    gap_counter = Counter()
    gap_total = 0
    sentence_total = 0
    sentence_match = 0
    aligned_total = 0
    work_linecount_equal = 0
    samples = []
    unmatched = []
    for work in target_works:
        text = work.get("text") or ""
        key = norm(text)[:8]
        candidates = by_first.get(key, [])
        poet = (work.get("poet") or {})
        poet_name = poet.get("name") if isinstance(poet, dict) else str(poet or "")
        filtered = [
            item for item in candidates
            if norm(item.get("authorName") or "") and norm(item.get("authorName") or "") in norm(poet_name)
        ]
        pool = filtered or candidates
        if len(pool) != 1:
            stats["not_uniquely_located"] += 1
            unmatched.append({"work": work.get("id"), "title": work.get("title"), "candidates": len(pool)})
            continue
        stats["located"] += 1
        item = pool[0]
        lib_content = item.get("content") or []
        lib_pinyin = item.get("pinyin") or []
        lib_norm = norm("".join(lib_content))
        api_norm = norm(text)
        if lib_norm == api_norm:
            stats["text_identical"] += 1
        else:
            stats["text_differs"] += 1
        api_lines = sorted((work.get("prosody") or {}).get("lines") or [], key=lambda line: line["line_no"])
        # 库 pinyin 串内部按句号切分即为句号级音节流，与官方句粒度一致
        lib_sentences = []
        for pinyin_text in (item.get("pinyin") or []):
            for segment in re.split(r"[。？！!?]", pinyin_text or ""):
                tokens = [token for token in segment.split() if token and not set(token) <= PUNCT]
                if tokens:
                    lib_sentences.append(tokens)
        if len(api_lines) == len(lib_sentences):
            stats["same_line_count"] += 1
        else:
            stats["line_count_differs"] += 1
        for index, api_line in enumerate(api_lines):
            sentence_total += 1
            if index >= len(lib_sentences):
                continue
            tokens = lib_sentences[index]
            api_chars = api_line["character_count"]
            if len(tokens) != api_chars:
                # 库内拼音漏标或多标：该句无法直接比对平仄，单独统计
                stats["lines_pinyin_length_mismatch"] += 1
                gap_total += api_chars - len(tokens)
                gap_counter[api_chars - len(tokens)] += 1
                continue
            stats["lines_aligned"] += 1
            classes = [tone_of(token) for token in tokens]
            ping = sum(1 for item in classes if item == "ping")
            ze = sum(1 for item in classes if item == "ze")
            unknown = len(classes) - ping - ze
            if unknown:
                stats["lines_with_neutral_tone"] += 1
            lib_ok = ping == api_line["ping_count"] and ze == api_line["ze_count"]
            # 同口径对照：pypinyin 首读音在同一句上的表现
            py_classes = []
            for syllable in pinyin(api_line["text"], style=Style.TONE3, heteronym=False, errors="ignore"):
                token = syllable[0] if isinstance(syllable, (list, tuple)) else syllable
                token = token or ""
                tail = token[-1] if token else ""
                py_classes.append("ping" if tail in "12" else ("ze" if tail in "34" else None))
            py_ping = sum(1 for item in py_classes if item == "ping")
            py_ze = sum(1 for item in py_classes if item == "ze")
            py_ok = py_ping == api_line["ping_count"] and py_ze == api_line["ze_count"]
            if lib_ok:
                stats["lib_pinyin_match"] += 1
            if py_ok:
                stats["pypinyin_match"] += 1
            if lib_ok or py_ok:
                stats["either_match"] += 1
            if not lib_ok and not py_ok:
                stats["neither_match"] += 1
            if lib_ok:
                sentence_match += 1
                stats["sentence_counts_match"] += 1
            else:
                stats["sentence_counts_differ"] += 1
                if len(samples) < 12:
                    samples.append({
                        "work": work.get("id"),
                        "title": work.get("title"),
                        "line_no": api_line["line_no"],
                        "api_line": api_line["text"][:40],
                        "api": [api_line["ping_count"], api_line["ze_count"]],
                        "lib": [ping, ze],
                        "unknown": unknown,
                    })

    report = {
        "scope": "官方%s快照 vs poetry-source %s词库（自带带调拼音）" % (dynasty, dynasty),
        "official_works": len(target_works),
        "library_works": len(library),
        "stats": dict(stats),
        "sentence_total": sentence_total,
        "aligned_sentences": stats["lines_aligned"],
        "aligned_match": sentence_match,
        "aligned_match_rate": round(sentence_match / stats["lines_aligned"], 4) if stats["lines_aligned"] else 0,
        "pinyin_gap_total_chars": gap_total,
        "pinyin_gap_distribution": dict(sorted(gap_counter.items())),
        "unmatched_samples": unmatched[:20],
        "mismatch_samples": samples,
    }
    (ROOT / "data" / "results" / out_name).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("unmatched_samples", "mismatch_samples")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

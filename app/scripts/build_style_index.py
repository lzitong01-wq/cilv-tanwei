#!/usr/bin/env python3
"""构建词人风格特征索引（Tier 2a 清词语料）。

分工：本脚本只算**可复算的形式特征**，不写任何风格判断。
风格描述由模型基于这些特征生成候选，且必须标未验证。

用法：python app/scripts/build_style_index.py [--corpus DIR] [--out PATH]
退出码：0 成功；1 语料缺失；2 参数错误。
"""

import argparse
import collections
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_ROOT = APP_ROOT.parent / ".workbuddy" / "reference" / "tier2"
# 风格画像覆盖清词 + 宋词两层：同一位词人在两层里合并统计
DEFAULT_CORPUS_DIRS = [
    REFERENCE_ROOT / "qing",
    REFERENCE_ROOT / "song",
]
DEFAULT_CORPUS = DEFAULT_CORPUS_DIRS[0]
DEFAULT_OUT = APP_ROOT / "data" / "style.sqlite3"

CJK_START, CJK_END = "\u3400", "\u9fff"
SENTENCE = re.compile(r"[。！？!?]")
MIN_WORKS = 20

IMAGE_WORDS = {
    "山水": ["山", "水", "江", "溪", "峰", "岭", "泉", "石", "林", "云", "月", "风"],
    "咏物": ["梅", "兰", "竹", "菊", "荷", "柳", "桃", "杏", "梧", "桐"],
    "相思": ["思", "忆", "念", "愁", "怨", "恨", "泪", "梦", "魂"],
    "怀古": ["古", "昔", "当年", "旧", "故", "遗迹"],
    "边塞": ["边", "塞", "胡", "朔", "沙", "戈", "笳", "营"],
    "闺阁": ["闺", "楼", "帘", "窗", "帐", "屏", "钗", "簪"],
}

# 带调字母 → 平/仄。一二声平，三四声仄；无调号视为轻声（不计入）。
TONE_PING = "āáēéīíōóūúǖǘ"
TONE_ZE = "ǎàěèǐìǒòǔùǚǜ"


def cjk_only(text: str) -> str:
    return "".join(ch for ch in (text or "") if CJK_START <= ch <= CJK_END)


def tone_class(syllable: str) -> str | None:
    for char in reversed(syllable):
        if char in TONE_PING:
            return "ping"
        if char in TONE_ZE:
            return "ze"
    return None


def load_corpus(corpus: Path) -> dict:
    """读一个语料层（清词或宋词目录）。

    目录里的文件名是 `ci.<朝代>.<分片>.*.json`，所以用 `ci.*` 通配而不是写死「清」——
    这样清词与宋词两个目录都能进，风格画像覆盖 4.3 万首而不是只 2.7 万首。
    """
    library = {}
    for path in sorted(corpus.glob("ci.*.json")):
        if path.name.endswith(".base.json") or path.name.endswith(".pinyin.json"):
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item.get("id"):
                library.setdefault(item["id"], item)
    for path in sorted(corpus.glob("ci.*.pinyin.json")):
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item.get("id") and item.get("pinyin"):
                library.setdefault(item["id"], {})["pinyin"] = item["pinyin"]
    return library


def features_for(items: list[dict]) -> dict:
    tunes = collections.Counter()
    lengths: list[int] = []
    ze_ratios: list[float] = []
    long_rates: list[float] = []
    images = collections.Counter()

    for item in items:
        text = "".join(item.get("content") or [])
        tune = (item.get("title") or "").split("·")[0]
        if tune:
            tunes[tune] += 1
        parts = [part for part in SENTENCE.split(text) if cjk_only(part)]
        counts = [len(cjk_only(part)) for part in parts]
        if not counts:
            continue
        lengths.extend(counts)
        threshold = math.ceil(sum(counts) / len(counts))
        long_rates.append(sum(1 for count in counts if count >= threshold) / len(counts))

        pinyin_text = " ".join(item.get("pinyin") or [])
        if pinyin_text:
            ping = ze = 0
            for syllable in pinyin_text.split():
                tone = tone_class(syllable)
                if tone == "ping":
                    ping += 1
                elif tone == "ze":
                    ze += 1
            if ping + ze:
                ze_ratios.append(ze / (ping + ze) * 100)

        for label, words in IMAGE_WORDS.items():
            hits = sum(text.count(word) for word in words)
            if hits:
                images[label] += hits

    if not lengths:
        return {}

    mean_length = sum(lengths) / len(lengths)
    variance = sum((value - mean_length) ** 2 for value in lengths) / len(lengths)
    return {
        "works": len(items),
        "tune_diversity": len(tunes),
        "top_tunes": tunes.most_common(3),
        "mean_line_length": round(mean_length, 2),
        "line_length_sd": round(math.sqrt(variance), 2),
        "ze_ratio": round(sum(ze_ratios) / len(ze_ratios), 2) if ze_ratios else None,
        "long_line_rate": round(sum(long_rates) / len(long_rates), 3),
        "top_images": images.most_common(3),
    }


def build(corpus_dirs: list[Path], out: Path) -> dict:
    """`corpus_dirs` 可以给多层（清词 + 宋词）；同一位词人在两层里合并统计。"""
    if not corpus_dirs:
        raise FileNotFoundError("没有语料目录")
    library: dict = {}
    for folder in corpus_dirs:
        if not folder.is_dir():
            continue
        library.update(load_corpus(folder))
    grouped = collections.defaultdict(list)
    for item in library.values():
        author = (item.get("authorName") or "").strip()
        if author and item.get("content"):
            grouped[author].append(item)

    rows = []
    for author, items in grouped.items():
        if len(items) < MIN_WORKS:
            continue
        stats = features_for(items)
        if stats:
            rows.append((author, stats))

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    connection = sqlite3.connect(out)
    try:
        connection.execute(
            """
            CREATE TABLE styles(
                poet TEXT PRIMARY KEY,
                works INTEGER, tune_diversity INTEGER,
                mean_line_length REAL, line_length_sd REAL,
                ze_ratio REAL, long_line_rate REAL,
                top_tunes TEXT, top_images TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO styles VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    author,
                    stats["works"],
                    stats["tune_diversity"],
                    stats["mean_line_length"],
                    stats["line_length_sd"],
                    stats["ze_ratio"],
                    stats["long_line_rate"],
                    json.dumps(stats["top_tunes"], ensure_ascii=False),
                    json.dumps(stats["top_images"], ensure_ascii=False),
                )
                for author, stats in rows
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return {"corpus_works": len(library), "authors": len(grouped), "indexed": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        action="append",
        default=None,
        help="语料目录，可给多次（清词 + 宋词）；不给则用默认的两个层",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    folders = [Path(item) for item in (args.corpus or DEFAULT_CORPUS_DIRS)]
    started = time.perf_counter()
    try:
        stats = build(folders, Path(args.out))
    except FileNotFoundError as exc:
        print("Tier 2 语料不在位：%s" % exc, file=sys.stderr)
        return 1
    cost = time.perf_counter() - started
    size = Path(args.out).stat().st_size / 1024
    print("风格索引已生成：%s" % args.out)
    print(
        "  语料 %d 首 · 作者 %d 位 · 入库 %d 位（作品数 ≥ %d）· %.0f KB · %.1fs"
        % (stats["corpus_works"], stats["authors"], stats["indexed"], MIN_WORKS, size, cost)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

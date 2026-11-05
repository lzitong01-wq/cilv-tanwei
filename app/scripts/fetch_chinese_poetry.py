#!/usr/bin/env python3
"""从 chinese-poetry 拉《全宋词》分片（**构建期输入**，不随交付分发）。

与 `fetch_tier2_dynasties.py` 互补：那边取 poetry-source 的带调拼音分片，
这边取 chinese-poetry 的**纯文本**分片（字段 `author/paragraphs/rhythmic`，
没有拼音）。两库都是官方题库 `参考资料` 字段列出的语料来源。

这批数据接入后按「今音计算」口径单独成层（Tier 2d），
与带调拼音层分开标注、不混算。

用法：python app/scripts/fetch_chinese_poetry.py [--dry-run]
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
OUT_DIR = REPO / ".workbuddy" / "reference" / "tier2" / "chinese_poetry"
RAW = "https://raw.githubusercontent.com/chinese-poetry/chinese-poetry/master/"
API = "https://api.github.com/repos/chinese-poetry/chinese-poetry/contents/"


def github_json(url: str):
    req = urllib.request.Request(
        url, headers={"User-Agent": "cilv-dev", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download(url: str, retries: int = 6) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cilv-dev"})
            with urllib.request.urlopen(req, timeout=90) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("下载失败（已重试 %d 次）：%r" % (retries, last))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    listing = github_json(API + urllib.parse.quote("宋词"))
    targets = sorted(
        item["name"] for item in listing
        if item["name"].startswith("ci.song.") and item["name"].endswith(".json")
    )
    targets = ["宋词/" + name for name in targets]
    # 不取元曲：那是「曲」不是「词」，题名会污染词牌统计，且只靠宋词已足够覆盖 58,851 首规模

    total = 0
    failed = []
    for name in targets:
        path = OUT_DIR / Path(name).name
        if args.dry_run:
            print("  将下载", name)
            continue
        payload = None
        if path.is_file() and path.stat().st_size > 100:
            cached = path.read_bytes()
            try:
                parsed = json.loads(cached.decode("utf-8"))
                if isinstance(parsed, list) and parsed:
                    payload = cached
            except Exception:
                payload = None
        if payload is None:
            # 路径按段编码，不能把 "/" 也编码掉
            url = RAW + "/".join(urllib.parse.quote(part) for part in name.split("/"))
            payload = download(url)
            path.write_bytes(payload)
        rows = json.loads(payload.decode("utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("items") or list(rows.values())[0] or []
        count = len(rows) if isinstance(rows, list) else 0
        total += count
        print("  %-24s %6d 条" % (name, count))
    print()
    print("%s：%d 个分片 / %s 条 / 失败 %d 个" % (
        "干跑完成" if args.dry_run else "下载完成", len(targets), format(total, ","), len(failed)
    ))
    for name, reason in failed:
        print("  ✗ %s：%s" % (name, reason))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

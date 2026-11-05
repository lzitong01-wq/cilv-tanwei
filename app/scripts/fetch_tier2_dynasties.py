#!/usr/bin/env python3
"""从 poetry-source 拉取剩余朝代的「词」分片（**构建期输入**，不随交付分发）。

背景：早期只接了清（26,742）与宋（16,449）共 43,191 首，而 poetry-source
的 `source/词/` 下还有明/元/唐/五代十国/金/隋/南北朝 7 个朝代，且**全部自带
`*.pinyin.json`**（带调拼音，与清/宋同口径）。规划里「两库去重后 5–6 万首」
的语料规模，主要就差这一部分。

为什么不用 chinese-poetry 的《全宋词》22 片：那批数据**没有拼音**，接进来
要引入「本地今音计算」的第四层口径，且与 poetry-source 宋词大量重复。
本脚本只取带调拼音的分片，保证三层口径仍然一致。

用法：
    python app/scripts/fetch_tier2_dynasties.py [--dry-run]
退出码：0 成功；1 失败。
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
REFERENCE = REPO / ".workbuddy" / "reference" / "tier2"
RAW = "https://raw.githubusercontent.com/snowtraces/poetry-source/master/source/%E8%AF%8D/"
API = "https://api.github.com/repos/snowtraces/poetry-source/contents/source/%E8%AF%8D/"

# 目录名（ASCII，避免 Windows 路径问题）→ 语料里的朝代名
DYNASTIES = {
    "ming": "明",
    "yuan": "元",
    "tang": "唐",
    "wudai": "五代十国",
    "jin": "金",
    "sui": "隋",
    "nanbeichao": "南北朝",
}


def github_json(url: str):
    req = urllib.request.Request(
        url, headers={"User-Agent": "cilv-dev", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download(url: str, retries: int = 4) -> bytes:
    """公开 CDN 偶发断连，重试即可；不重试会让整批下载半途而废。"""
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
    parser.add_argument("--dry-run", action="store_true", help="只列出将下载的分片，不写盘")
    args = parser.parse_args()

    total_files = 0
    total_works = 0
    for folder, dynasty in DYNASTIES.items():
        listing_url = API + urllib.parse.quote(dynasty)
        try:
            listing = github_json(listing_url)
        except Exception as exc:  # noqa: BLE001
            print("  ✗ %s 列目录失败：%r" % (dynasty, exc), file=sys.stderr)
            return 1
        names = sorted(
            item["name"] for item in listing if item["name"].endswith(".pinyin.json")
        )
        target_dir = REFERENCE / folder
        if not args.dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for name in names:
            url = RAW + urllib.parse.quote(dynasty) + "/" + urllib.parse.quote(name)
            if args.dry_run:
                print("  将下载 %s/%s" % (dynasty, name))
                total_files += 1
                continue
            path = target_dir / name
            payload = None
            if path.is_file() and path.stat().st_size > 100:
                cached = path.read_bytes()
                try:
                    parsed = json.loads(cached.decode("utf-8"))
                    if isinstance(parsed, list) and parsed:
                        payload = cached
                except Exception:
                    payload = None  # 半截文件，重下
            if payload is None:
                payload = download(url)
                path.write_bytes(payload)
            try:
                rows = json.loads(payload.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                print("  ✗ %s/%s 不是合法 JSON：%r" % (dynasty, name, exc), file=sys.stderr)
                return 1
            if not isinstance(rows, list):
                print("  ✗ %s/%s 不是数组" % (dynasty, name), file=sys.stderr)
                return 1
            # 格式必须与现有清/宋分片一致，否则构建脚本会静默丢数据
            if rows and not {"id", "title", "authorName", "dynasty", "content", "pinyin"} <= set(rows[0]):
                print("  ✗ %s/%s 字段不匹配：%s" % (dynasty, name, list(rows[0])), file=sys.stderr)
                return 1
            count += len(rows)
            total_files += 1
        total_works += count
        print("  %-6s %2d 个分片 / %6d 首 -> .workbuddy/reference/tier2/%s" % (dynasty, len(names), count, folder))

    print()
    print("%s：%d 个分片 / %s 首" % ("干跑完成" if args.dry_run else "下载完成", total_files, format(total_works, ",")))
    if not args.dry_run:
        print("说明：这些文件是**构建期输入**，不随交付包分发（见 .gitignore 与分册 C §C4）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""构建期字体子集化：把 OFL 授权的思源宋体裁成「本项目真正用到的字」。

为什么必须打包字体：龙蜥（Anolis）上没有楷体/宋体，`font-family: KaiTi, SimSun`
会整体落空，中文会掉到无中文字形的 serif 上。子集化后随前端产物分发，
既保证不缺字，也避免塞进 12 MB 的全字库。

用法（需先 pip install fonttools brotli）：
    python app/scripts/build_fonts.py [--src app/frontend/.fontsrc]
    # 可选：APP_QUIZ_DIR 指向题库目录，把题面与答案里的字也纳入字符集

字符集来源（对应前端计划 §8：「423 篇 + 题库 + UI 文案」）：
    Tier 1 快照正文与题名 ∪ 前端源码 ∪ 风格索引词人名 ∪ 本地题库（若在位）∪ 常用标点

产物：
    app/frontend/public/fonts/NotoSerifSC-Regular.subset.woff2
    app/frontend/public/fonts/NotoSerifSC-Medium.subset.woff2

授权：SIL Open Font License 1.1（见同目录 OFL.txt），允许子集化与再分发。
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
APP = REPO / "app"
FRONTEND = APP / "frontend"
SNAPSHOT = REPO / "data" / "知识库审计快照V1.jsonl"
STYLE_DB = APP / "data" / "style.sqlite3"
QUIZ_FILES = (
    "公开测试集_700题.jsonl",
    "保密验证集_300题_题面.jsonl",
    "保密验证集_300题_答案.jsonl",
)


def quiz_dirs() -> list[Path]:
    candidates = []
    env = os.environ.get("APP_QUIZ_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / "Downloads" / "薪火人文-清词-1000题库-V5版本")
    return candidates

# 界面与标点：源码里出现的中文已覆盖大部分，这里补齐常用标点与全角符号
EXTRA = (
    "　、。，．；：？！…—～·《》〈〉「」『』（）【】〔〕［］｛｝“”‘’"
    "0123456789０１２３４５６７８９"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    "○●◌◆◇■□▲△▼▽★☆→←↑↓≥≤±×÷"
    # 题库题面措辞里的常见字。题库不在位时也要保底，否则解题页显示题面会掉字。
    "仅优劣哪率配阈＞＜"
)


def chars_from_snapshot() -> set[str]:
    chars: set[str] = set()
    if not SNAPSHOT.is_file():
        print("跳过快照（不存在）：%s" % SNAPSHOT, file=sys.stderr)
        return chars
    with SNAPSHOT.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            chars.update(str(record.get("title") or ""))
            chars.update(str(record.get("tune_pattern") or ""))
            chars.update(str(record.get("period_label") or ""))
            chars.update(str(record.get("text") or ""))
            poet = record.get("poet") or {}
            if isinstance(poet, dict):
                chars.update(str(poet.get("name") or ""))
                chars.update(str(poet.get("dynasty") or ""))
    return chars


def chars_from_frontend() -> set[str]:
    chars: set[str] = set()
    for pattern in ("*.tsx", "*.ts", "*.css", "*.html"):
        for path in FRONTEND.glob("src/**/" + pattern) if pattern != "*.html" else FRONTEND.glob(pattern):
            chars.update(path.read_text(encoding="utf-8"))
    return chars


def chars_from_style_db() -> set[str]:
    chars: set[str] = set()
    if not STYLE_DB.is_file():
        return chars
    connection = sqlite3.connect("file:%s?mode=ro" % STYLE_DB.as_posix(), uri=True)
    try:
        for (name,) in connection.execute("SELECT DISTINCT poet FROM authors"):
            chars.update(str(name))
    except sqlite3.Error:
        pass
    finally:
        connection.close()
    return chars


def chars_from_quiz() -> set[str]:
    """题面会在解题页、评测中心逐题下钻里显示，字必须也在子集里。

    题库是外部输入、不随交付物分发，所以在位就扫描、不在位就靠 EXTRA 兜底，
    两种情况都不能让构建失败。
    """
    chars: set[str] = set()
    base = next((path for path in quiz_dirs() if path.is_dir()), None)
    if base is None:
        print("题库不在位，字符集用 EXTRA 兜底（设 APP_QUIZ_DIR 可纳入题面全字集）", file=sys.stderr)
        return chars
    for name in QUIZ_FILES:
        path = base / name
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                for value in record.values():
                    if isinstance(value, str):
                        chars.update(value)
    print("题库字集：%s（%d 字）" % (base, len(chars)))
    return chars


def keep_char(char: str) -> bool:
    """只留真正要渲染的字：跳过控制字符与私用区。"""
    code = ord(char)
    if char in "\n\r\t":
        return False
    if 0xE000 <= code <= 0xF8FF:
        return False
    return code >= 0x20


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(FRONTEND / ".fontsrc"))
    parser.add_argument("--out", default=str(FRONTEND / "public" / "fonts"))
    args = parser.parse_args()

    source_dir = Path(args.src)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from fontTools import subset
    except ImportError:
        print("缺少 fonttools：pip install fonttools brotli", file=sys.stderr)
        return 2

    chars = (
        chars_from_snapshot()
        | chars_from_frontend()
        | chars_from_style_db()
        | chars_from_quiz()
    )
    chars.update(EXTRA)
    chars = {char for char in chars if keep_char(char)}
    text = "".join(sorted(chars))

    charset_file = source_dir / "charset.txt"
    charset_file.parent.mkdir(parents=True, exist_ok=True)
    charset_file.write_text(text, encoding="utf-8")
    print("字符集 %d 字 → %s" % (len(text), charset_file))

    for weight in ("Regular", "Medium"):
        source = source_dir / ("NotoSerifSC-%s.otf" % weight)
        if not source.is_file():
            print("缺少源字体：%s" % source, file=sys.stderr)
            return 2
        target = out_dir / ("NotoSerifSC-%s.subset.woff2" % weight)
        options = subset.Options()
        options.flavor = "woff2"
        # 实测：CFF 去掉 subroutine 后 woff2 反而更小（≈750 KB vs 822 KB）
        options.desubroutinize = True
        # 页面不用竖排与复杂排版特性，去掉这些表每字重省约 4 KB；字形轮廓才是大头
        options.layout_features = []
        options.name_IDs = [1, 2, 4, 6]
        options.notdef_outline = False
        options.glyph_names = False
        options.drop_tables += [
            "DSIG", "GSUB", "GPOS", "GDEF", "BASE", "JSTF", "MATH",
            "EBDT", "EBLC", "VORG", "vhea", "vmtx",
        ]
        font = subset.load_font(str(source), options)
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(text=text)
        subsetter.subset(font)
        subset.save_font(font, str(target), options)
        print("%-8s %6.1f KB → %s" % (weight, target.stat().st_size / 1024, target.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())

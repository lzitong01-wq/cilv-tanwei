"""字体子集覆盖测试：打包字体必须覆盖语料与界面用到的每一个字。

为什么值得单独测：龙蜥上没有楷体/宋体，界面完全依赖这两个子集文件。
一旦语料扩容后忘了重跑 `scripts/build_fonts.py`，界面就会在真机上掉字，
而本机（Windows/macOS）因为系统字体兜底完全看不出来——这是最典型的"本地好、上线坏"。

需要 fonttools；未安装时整组跳过，不阻塞 CI。
"""

import json
import os
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
FONT_DIR = APP / "frontend" / "public" / "fonts"
SNAPSHOT = REPO / "data" / "知识库审计快照V1.jsonl"
QUIZ_FILES = (
    "公开测试集_700题.jsonl",
    "保密验证集_300题_题面.jsonl",
    "保密验证集_300题_答案.jsonl",
)

try:
    from fontTools.ttLib import TTFont

    HAVE_FONTTOOLS = True
except ImportError:  # pragma: no cover - 环境缺依赖时跳过
    HAVE_FONTTOOLS = False


def corpus_chars() -> set[str]:
    chars: set[str] = set()
    if not SNAPSHOT.is_file():
        return chars
    with SNAPSHOT.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            chars.update(str(record.get("title") or ""))
            chars.update(str(record.get("tune_pattern") or ""))
            chars.update(str(record.get("text") or ""))
            poet = record.get("poet") or {}
            if isinstance(poet, dict):
                chars.update(str(poet.get("name") or ""))
    return {char for char in chars if char.strip()}


def quiz_dir() -> Path | None:
    candidates = []
    env = os.environ.get("APP_QUIZ_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / "Downloads" / "薪火人文-清词-1000题库-V5版本")
    return next((path for path in candidates if path.is_dir()), None)


def quiz_chars() -> set[str]:
    """题面会显示在解题页与评测中心逐题下钻里，字也必须在子集里。"""
    base = quiz_dir()
    chars: set[str] = set()
    if base is None:
        return chars
    for name in QUIZ_FILES:
        path = base / name
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                for value in json.loads(line).values():
                    if isinstance(value, str):
                        chars.update(value)
    return {char for char in chars if char.strip()}


# 已知超出思源宋体覆盖范围的字：源字体本身没有该字形，不是子集漏掉。
# U+284E6 属 CJK 扩展 B，出现在 48783《南乡子·迨𨓦碧河酒楼题壁》题名里，
# 浏览器会回落到系统字体；若这里超过 5 个，说明该换字体或另配回退字体。
KNOWN_MISSING = {"𨓦"}
MAX_KNOWN_MISSING = 5


@unittest.skipUnless(HAVE_FONTTOOLS, "需要 fonttools：pip install fonttools brotli")
class FontSubsetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fonts = {}
        for weight in ("Regular", "Medium"):
            path = FONT_DIR / ("NotoSerifSC-%s.subset.woff2" % weight)
            if not path.is_file():
                raise unittest.SkipTest("字体子集未生成：%s" % path)
            font = TTFont(str(path))
            cls.fonts[weight] = set(font.getBestCmap().keys())

    def test_corpus_is_fully_covered(self):
        missing = {
            char
            for char in corpus_chars()
            if ord(char) not in self.fonts["Regular"] and char not in KNOWN_MISSING
        }
        self.assertFalse(missing, "语料中有 %d 字不在字体子集里：%s" % (len(missing), "".join(sorted(missing))[:50]))

    def test_known_missing_stays_small(self):
        """白名单只放源字体确实没有的字；变多就说明该换字体，而不是继续加豁免。"""
        actual = {
            char
            for char in corpus_chars() | quiz_chars()
            if ord(char) not in self.fonts["Regular"]
        }
        self.assertLessEqual(len(actual), MAX_KNOWN_MISSING, "字体缺字超过阈值：%s" % "".join(sorted(actual)))
        self.assertTrue(actual <= KNOWN_MISSING, "出现未登记缺字：%s" % "".join(sorted(actual - KNOWN_MISSING)))

    @unittest.skipUnless(quiz_dir(), "题库不在位，题面覆盖由构建脚本的 EXTRA 兜底")
    def test_quiz_text_is_covered(self):
        """前端计划 §8 要求字符集含题库；题面在龙蜥上缺字会比语料缺字更显眼。"""
        missing = {
            char
            for char in quiz_chars()
            if ord(char) not in self.fonts["Regular"] and char not in KNOWN_MISSING
        }
        self.assertFalse(missing, "题库有 %d 字不在子集里：%s" % (len(missing), "".join(sorted(missing))[:50]))

    def test_both_weights_share_coverage(self):
        self.assertEqual(self.fonts["Regular"], self.fonts["Medium"])

    def test_common_punctuation_present(self):
        for char in "，。、；：？！《》「」—…·":
            self.assertIn(ord(char), self.fonts["Regular"], "缺标点 %s" % char)

    def test_subset_is_small_enough(self):
        """全字库 12 MB；子集应远小于此，否则说明字符集被污染。"""
        for weight in ("Regular", "Medium"):
            size = (FONT_DIR / ("NotoSerifSC-%s.subset.woff2" % weight)).stat().st_size
            self.assertLess(size, 1_600_000, "%s 子集过大：%.1f KB" % (weight, size / 1024))
            self.assertGreater(size, 200_000, "%s 子集过小，可能缺字" % weight)


if __name__ == "__main__":
    unittest.main(verbosity=2)

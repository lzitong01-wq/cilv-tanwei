"""逐字读音标注的唯一定实现（本地字典口径）。

库内作品（`/api/works/{id}/chars`）与自由文本（`/api/parse`）共用这一处，
避免两套口径分叉。这里的四声**不是官方标注**：官方 prosody 只提供逐句
ping/ze 计数，逐字声调整体由 pypinyin 首读推算，必须如实标注来源。

不能只用「CJK 基本区」正则过滤字符：扩展 B 的生僻字（如 𨓦 U+284E6）
会被静默丢掉，等于把「未知」藏起来（违反 INV-UNK-05）。这里改成
「跳过标点与空白，其余字符一律给条目」，查不到读音就显式标 unknown。
"""

from pypinyin import Style, pinyin

# 句读与空白：不算字，也不进字格
IGNORED = set(
    "　 \t\r\n，。、．；：？！…—～·《》〈〉「」『』（）【】〔〕［］｛｝“”‘’"
    "()[]{}<>.,;:!?\"'`~@#$%^&*-_=+|\\/"
)
TONE_NAMES = {1: "阴平", 2: "阳平", 3: "上", 4: "去", 0: "轻声"}
SOURCE = "local_dict"
TIER_LABEL = "本地计算"


def content_chars(text: str) -> list[str]:
    """保留所有非标点非空白字符，包括扩展区汉字与查不到读音的字。"""
    return [char for char in (text or "") if char not in IGNORED and not char.isspace()]


def _tone_of(token: str) -> int:
    tail = token[-1] if token else ""
    return int(tail) if tail.isdigit() else 0


def _ping_ze(tone: int) -> str:
    if tone in (1, 2):
        return "平"
    if tone in (3, 4):
        return "仄"
    return "未知"


def annotate(text: str) -> list[dict]:
    """把一句词切成逐字读音项；未知读音显式给出 unknown_reason。

    逐字调用 pypinyin 而不是整句批量：批量模式下遇到字典不认识的字会跳过该项，
    导致读音数组与字符数组错位，后面的字全会串位。
    """
    items = []
    for char in content_chars(text):
        reading = pinyin(char, style=Style.TONE3, heteronym=False, errors="ignore")
        token = reading[0][0] if reading and reading[0] else ""
        variants = pinyin(char, style=Style.TONE3, heteronym=True, errors="ignore")
        pool: list[str] = []
        for variant in variants[0] if variants else []:
            if variant and variant not in pool:
                pool.append(variant)
        # 非汉字或字典未收录时 pypinyin 会原样回显字符，这里不把它当读音
        if token == char:
            token = ""
        pool = [item for item in pool if item != char]
        tone = _tone_of(token)
        items.append(
            {
                "char": char,
                "pinyin": token,
                "tone": tone,
                "tone_name": TONE_NAMES.get(tone, "未知"),
                "ping_ze": _ping_ze(tone),
                "source": SOURCE,
                "tier_label": TIER_LABEL,
                "candidates": pool,
                "unknown_reason": None if tone else "字典无该字声调记录",
            }
        )
    return items


def count(items: list[dict]) -> tuple[int, int]:
    """本地口径的平/仄计数（与官方逐句计数不是一回事，界面须分别标注）。"""
    ping = sum(1 for item in items if item["ping_ze"] == "平")
    ze = sum(1 for item in items if item["ping_ze"] == "仄")
    return ping, ze

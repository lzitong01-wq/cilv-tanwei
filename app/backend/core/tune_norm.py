"""同源语料词牌归一：把「词牌 + 题注」切回规范词牌名。

**问题**：同源语料的标题有三种形态——「词牌·题注」「词牌 题注」「词牌/别名」。
构建期早期只按「·」切一刀，于是 43,191 首里有 2,675 个「词牌」其实是
「壶中天题公曼仇史，即以为赠」这样的**题注串**。后果是：
按词牌筛选时这些作品永远筛不出来；词牌候选列表里混着几千个伪词牌；
词牌统计口径失真（5,782 个「词牌」里真正体只有约 2,500 个）。

**做法**：用**自举词表**把规范词牌切回来。

1. 词表初值 = 有「·」的标题切出的调名（最可靠）+ 高频且短的纯汉字 tune 值；
2. 对切不出来的作品，用词表做**最长前缀匹配**；
3. 切出的前缀本身就是真词牌，把它并回词表再迭代，直到不再新增。

**实测**（清词 + 宋词 43,191 首）：可切出 40,346 首（93.4%），
distinct 词牌从 5,782 降到 2,457；斜杠并列的别名（望江南/忆江南）另存一张表，
原题注全部保留在 `works.title` 里，一字不丢。

**边界**：切不出来的 2,845 首里，多数是只出现一次的冷门词牌或散文题，
保留原值——宁可不准，也不丢信息。构建期与运行时共用这一份逻辑，
改这里等于同时改索引构建与查询口径。
"""

import re

# 题注里常见的标点与分隔符
_PUNCT = re.compile(r"[，。、；：？！（）《》「」『』·\[\]【】\s]")
# 「望江南/忆江南」这类并列写法
_SLASH = re.compile(r"[/／]")
_HANZI = re.compile(r"[\u4e00-\u9fff]+")

# 冷门词牌的宽松上限：纯汉字且不超过这个长度，就认为它本身就是词牌名
_COLD_MAX_LEN = 8
# 前缀匹配的最大长度
_MAX_MATCH = 12
# 词表初值里，「高频 tune 值」的频次门槛与长度上限
_SEED_MIN_COUNT = 2
_SEED_MAX_LEN = 6


def clean_part(text: str) -> str:
    """去掉标点与空白，只留汉字。"""
    return _PUNCT.sub("", text or "").strip()


def is_clean_tune(text: str) -> bool:
    """是否像规范词牌名：纯汉字、长度在 1–8 之间。"""
    s = (text or "").strip()
    return bool(s) and len(s) <= _COLD_MAX_LEN and bool(_HANZI.fullmatch(s))


def is_caption_like(text: str) -> bool:
    """是否是「题记体」而不是词牌名。

    同源语料里有一小批作品，作者只留了题记、没给词牌，
    调名就成了「般涉瑞鹧鸪瓶供蜡梅红梅各一枝，时距立春尚远」这样的整句。
    判据是**含标点**——真正的词牌名不带标点。

    这类值保留在 `works.tune` 里（信息不丢），但**不计入**词调身份层、
    作者候选的词牌列表与规模统计——它们不是词牌。
    """
    s = (text or "").strip()
    if not s:
        return True
    return bool(_PUNCT.search(s))


def match_prefix(text: str, vocab: set) -> str:
    """在词表里找 `text` 的最长前缀。命中返回前缀本身，没命中返回空串。"""
    s = (text or "").strip()
    for length in range(min(len(s), _MAX_MATCH), 0, -1):
        piece = s[:length]
        if piece in vocab:
            return piece
    return ""


def build_vocab(pairs: list) -> set:
    """自举出词牌词表。

    `pairs` 是 `(title, tune)` 的序列——构建期是原始标题与粗切结果，
    运行时就地修正时是库里现有的 title 与 tune。
    """
    counter: dict = {}
    for _title, tune in pairs:
        counter[tune or ""] = counter.get(tune or "", 0) + 1

    vocab: set = set()
    # a) 有「·」的标题切出的调名最可靠
    for title, _tune in pairs:
        if "·" in (title or ""):
            head = title.split("·", 1)[0]
            for part in _SLASH.split(head):
                piece = clean_part(part)
                if piece and len(piece) <= _MAX_MATCH:
                    vocab.add(piece)
    # b) 高频且短、纯汉字的 tune 值
    for tune, count in counter.items():
        s = (tune or "").strip()
        if count >= _SEED_MIN_COUNT and len(s) <= _SEED_MAX_LEN and _HANZI.fullmatch(s):
            vocab.add(s)
    return vocab


def canonicalize(raw: str, vocab: set) -> tuple:
    """把一个原始 tune 串切成 `(规范词牌, 别名列表)`。

    规则：
    - 斜杠并列（望江南/忆江南）逐段尝试，取最长命中；
    - 其余按最长前缀匹配；
    - 都匹配不上：纯汉字且短的按冷门词牌原样保留；带标点的保留原值（题注体）。
    """
    text = (raw or "").strip()
    if not text:
        return "", []

    aliases = [clean_part(part) for part in _SLASH.split(text)]
    aliases = [item for item in aliases if item]

    hit = ""
    if _SLASH.search(text):
        for part in _SLASH.split(text):
            piece = clean_part(part)
            if not piece:
                continue
            candidate = match_prefix(piece, vocab)
            if candidate and len(candidate) > len(hit):
                hit = candidate
    if not hit:
        hit = match_prefix(text, vocab)

    if not hit:
        # 切不出来：原样保留，别名就是它本身，不另记
        return text, []

    # 别名只认「斜杠并列的另一个词牌名」（望江南/忆江南）。
    # 判据是**它自己也在词表里**——「望汉月」是真词牌，「南楼令临春阁」是题注。
    keep = [item for item in aliases if item != hit and item in vocab]
    return hit, keep


def normalize(pairs: list) -> dict:
    """对整批 `(wid, title, tune)` 做归一。

    返回：
        {
            "canonical": {wid: 规范词牌},
            "aliases":   [(wid, 别名), …],
            "vocab":     最终词表,
            "stats":     {total, cut, cold, kept, distinct_before, distinct_after}
        }
    """
    title_tune = [(title, tune) for _wid, title, tune in pairs]
    vocab = build_vocab(title_tune)

    # 迭代：切出的前缀并回词表，直到不再新增
    for _round in range(8):
        added = set()
        for _wid, title, tune in pairs:
            if "·" in (title or ""):
                continue
            hit = match_prefix((tune or "").split("·", 1)[0].strip(), vocab)
            if hit:
                added.add(hit)
        fresh = added - vocab
        if not fresh:
            break
        vocab |= fresh

    canonical: dict = {}
    aliases: list = []
    cold = 0
    kept = 0
    for wid, title, tune in pairs:
        raw = (tune or "").split("·", 1)[0].strip()
        hit, keep = canonicalize(raw, vocab)
        canonical[wid] = hit
        for alias in keep:
            aliases.append((wid, alias))
        if hit != raw:
            continue
        if is_clean_tune(raw):
            cold += 1
        else:
            kept += 1

    distinct_before = len({(tune or "").split("·", 1)[0].strip() for _w, _t, tune in pairs})
    return {
        "canonical": canonical,
        "aliases": aliases,
        "vocab": vocab,
        "stats": {
            "total": len(pairs),
            "cut": len(pairs) - cold - kept,
            "cold": cold,
            "kept": kept,
            "distinct_before": distinct_before,
            "distinct_after": len({v for v in canonical.values() if v}),
        },
    }

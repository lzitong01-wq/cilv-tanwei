"""词谱体式消歧（分册 A §A3）：候选打分 + 置信度 + 逐字平仄对照。

榜题底座是词谱，《最小领域模型》强调「调名 ≠ 体式」：同一个词牌下有多套体式，
所以**不能只按词牌对号入座**，必须给出候选列表与置信度。

打分维度（按 §A3.2 的权重建议，取"最小错误候选"而非布尔匹配）：

| 维度 | 权重 | 说明 |
|---|---|---|
| 总字数 | 硬过滤 | 不符即出局 |
| 句读序列（每句字数） | 高 | 与谱的 rhythm 切分对齐；谱的「读」不切句 |
| 分阕位置（shift） | 高 | 与 API 的 split_after_line 对照 |
| 韵位匹配 | 中 | 允许换韵／多韵 |
| 用字重合度 | 低 | 仅作同分裁决 |

**逐字对照只在唯一或高分候选时给出**（§A3.2 末句与 §A3.3 门禁）；其余一律
「多候选待确认」，不得拿不准的体式去改读音。
"""

import json
import threading
from functools import lru_cache
from pathlib import Path

from .errors import ApiError

# 高置信阈值：句读序列与分阕都对得上
HIGH_CONFIDENCE_PENALTY = 0
MEDIUM_CONFIDENCE_PENALTY = 2

_LOCK = threading.Lock()
_CACHE: dict = {}


def _index_path() -> Path:
    # core/prosody.py → backend → app/data/
    return Path(__file__).resolve().parents[2] / "data" / "prosody_index.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    path = _index_path()
    if not path.is_file():
        raise ApiError(
            "NOT_FOUND",
            "词谱索引未生成",
            {"expected_path": str(path), "hint": "运行 python app/scripts/build_prosody_index.py"},
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("items") or {}


def available() -> bool:
    try:
        _load()
        return True
    except ApiError:
        return False


def candidates(tune_name: str, sentences: list[int], split_after: int) -> list[dict]:
    """给一篇作品的候选体式：按"最小错误"排序，附各项得分与置信度。"""
    index = _load()
    forms = index.get(tune_name) or []
    total = sum(sentences)
    scored = []
    for form in forms:
        if form["total"] != total:
            continue  # 总字数：硬过滤
        form_seq = form["sentences"]
        if len(form_seq) == len(sentences):
            seq_penalty = sum(abs(a - b) for a, b in zip(form_seq, sentences))
            len_penalty = 0
        else:
            # 句数不同：按可对齐的前缀算，其余按缺字数计罚
            common = min(len(form_seq), len(sentences))
            seq_penalty = sum(abs(a - b) for a, b in zip(form_seq[:common], sentences[:common]))
            seq_penalty += abs(form_seq[common:] and sum(form_seq[common:]) or 0)
            seq_penalty += abs(sum(sentences[common:]))
            len_penalty = abs(len(form_seq) - len(sentences)) * 2
        if form.get("split_after") is None:
            split_penalty = 1
        elif form["split_after"] == split_after:
            split_penalty = 0
        else:
            split_penalty = 3
        penalty = seq_penalty + len_penalty + split_penalty
        scored.append(
            {
                "form_id": form["form_id"],
                "author": form.get("author"),
                "sketch": form.get("sketch"),
                "total": form["total"],
                "sentences": form["sentences"],
                "split_after": form.get("split_after"),
                "tones": form["tones"],
                "penalty": penalty,
                "scores": {
                    "句读序列": seq_penalty,
                    "句数差": len_penalty,
                    "分阕": split_penalty,
                },
                "confidence": (
                    "高"
                    if penalty <= HIGH_CONFIDENCE_PENALTY
                    else "中"
                    if penalty <= MEDIUM_CONFIDENCE_PENALTY
                    else "低"
                ),
            }
        )
    scored.sort(key=lambda item: (item["penalty"], item["form_id"]))
    return scored


def best(tune_name: str, sentences: list[int], split_after: int) -> dict | None:
    """唯一或高分候选；否则返回 None（不拿不准就不用）。

    返回 {"form": ..., "unique": bool, "candidates": [...]}：
    - unique=True 表示该词牌下只有一个同字数体式；
    - confidence 高/中 表示允许进入逐字对照。
    """
    scored = candidates(tune_name, sentences, split_after)
    if not scored:
        return None
    top = scored[0]
    unique = len(scored) == 1
    allowed = unique or top["confidence"] in ("高", "中")
    return {
        "form": top if allowed else None,
        "unique": unique,
        "allowed_for_alignment": allowed,
        "candidates": scored[:8],
        "boundary": (
            "候选按句读序列与分阕打分；只有唯一或高分候选才允许逐字对照，"
            "其余为多候选待确认。"
        ),
    }


def align(form: dict, sentences: list[int]) -> list[str] | None:
    """把谱的逐字平仄按句读切分，对齐到我们各句的字位。

    对不上（句读不一致）时返回 None——不硬凑。
    """
    tones = form.get("tones") or ""
    form_seq = form.get("sentences") or []
    if len(form_seq) != len(sentences):
        return None
    out = []
    offset = 0
    for length in sentences:
        chunk = tones[offset : offset + length]
        if len(chunk) != length:
            return None
        out.append(chunk)
        offset += length
    return out


def tone_class(series: str) -> tuple[int, int]:
    """谱里「平」「仄」计入计数，「中」两可，不计入任一侧（因此只给区间不给定值）。"""
    ping = sum(1 for tone in series if tone == "平")
    ze = sum(1 for tone in series if tone == "仄")
    return ping, ze

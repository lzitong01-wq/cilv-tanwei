#!/usr/bin/env python3
"""LLM 副功能探针：风格归纳 / 情感候选 / 推理链复述 / 越界抵抗。

设计前提：模型只消费"事实卡"，只产出候选解读，不产出或修改声律数值。
每个用例都有可程序判定的通过线，避免用"看起来不错"当验收。
"""

import argparse
import collections
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from pypinyin import Style, pinyin

CJK = re.compile(r"[㐀-鿿]")
ROOT = Path(__file__).resolve().parent.parent
TIER2 = ROOT / ".workbuddy" / "reference" / "tier2" / "qing"
# 韵部字表源自 chinese_word_rhyme（上游授权未确认），不随工作区分发；缺失时韵部特征按不可用处理
RHYME = ROOT / ".workbuddy" / "reference" / "Ci_Word_Tune.json"
BASE_URL = "http://10.27.66.12/v1/chat/completions"

# 公版意象词表（示例集，需人文扩充；仅用于统计"题材词频"这类形式特征）
IMAGE_WORDS = {
    "闺阁": ["闺", "楼", "帘", "窗", "罗帏", "香闺", "锦帐", "玉楼"],
    "边塞": ["边", "塞", "胡", "朔", "沙", "戈", "刀", "笳", "营", "虏", "金甲"],
    "山水": ["山", "水", "江", "溪", "峰", "岭", "泉", "石", "林", "月", "风", "云"],
    "咏物": ["梅", "兰", "竹", "菊", "荷", "柳", "桃", "杏", "梧", "桐"],
    "怀古": ["古", "昔", "当年", "旧", "陈", "迹", "故", "古道"],
    "悼亡": ["悼", "亡", "殁", "逝", "招魂", "冷落", "孤坟"],
    "相思": ["思", "忆", "念", "愁", "怨", "恨", "泪", "梦", "魂"],
}


def norm(text):
    return "".join(CJK.findall(text or ""))


def tone_of(token):
    tail = token[-1] if token else ""
    if tail in "12":
        return "ping"
    if tail in "34":
        return "ze"
    return None


def line_tones(text):
    counts = collections.Counter()
    for syllable in pinyin(text, style=Style.TONE3, heteronym=False, errors="ignore"):
        token = syllable[0] if isinstance(syllable, (list, tuple)) else syllable
        tone = tone_of(token or "")
        if tone:
            counts[tone] += 1
    return counts


def load_tier2():
    library = {}
    for path in sorted(TIER2.glob("ci.清.*.json")):
        if path.name.endswith(".base.json") or path.name.endswith(".pinyin.json"):
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item.get("id"):
                library.setdefault(item["id"], item)
    return library


def author_features(library, rhyme_map):
    by_author = collections.defaultdict(list)
    for item in library.values():
        author = (item.get("authorName") or "").strip()
        content = item.get("content") or []
        if author and content:
            by_author[author].append(item)
    features = {}
    for author, items in by_author.items():
        works = len(items)
        tunes = collections.Counter()
        lengths = []
        ze_ratios = []
        long_rate = []
        rhyme_counter = collections.Counter()
        image_counter = collections.Counter()
        for item in items:
            text = "".join(item.get("content") or [])
            tune = (item.get("title") or "").split("·")[0]
            tunes[tune] += 1
            sentences = [part for part in re.split(r"[。！？!?]", text) if CJK.search(part)]
            counts = [len(CJK.findall(part)) for part in sentences]
            if not counts:
                continue
            lengths.extend(counts)
            average = math.ceil(sum(counts) / len(counts))
            long_rate.append(sum(1 for c in counts if c >= average) / len(counts))
            total_ping = total_ze = 0
            for part in sentences:
                tone = line_tones(part)
                total_ping += tone["ping"]
                total_ze += tone["ze"]
            if total_ping + total_ze:
                ze_ratios.append(total_ze / (total_ping + total_ze) * 100)
            if sentences:
                last_char = CJK.findall(sentences[-1])
                if last_char:
                    entry = rhyme_map.get(last_char[-1]) or {}
                    if entry.get("rhyme"):
                        rhyme_counter[entry["rhyme"]] += 1
            for label, words in IMAGE_WORDS.items():
                hits = sum(text.count(word) for word in words)
                if hits:
                    image_counter[label] += hits
        if len(lengths) < 50:
            continue
        mean_length = sum(lengths) / len(lengths)
        variance = sum((x - mean_length) ** 2 for x in lengths) / len(lengths)
        features[author] = {
            "works": works,
            "tune_diversity": len(tunes),
            "top_tunes": tunes.most_common(3),
            "mean_line_length": round(mean_length, 2),
            "line_length_sd": round(math.sqrt(variance), 2),
            "mean_ze_ratio": round(sum(ze_ratios) / len(ze_ratios), 2) if ze_ratios else None,
            "mean_long_line_rate": round(sum(long_rate) / len(long_rate), 3) if long_rate else None,
            "top_rhyme_parts": rhyme_counter.most_common(3),
            "image_word_top": image_counter.most_common(3),
            "sample_work": (items[0].get("title") or "")[:40],
        }
    return features


def fact_card(features, authors):
    lines = []
    for author in authors:
        f = features[author]
        lines.append(
            "【%s】作品数%d；词牌数%d（常用%s）；平均句长%.2f字（标准差%.2f）；"
            "全文仄声比例均值%.2f%%；长句句占比均值%.3f；句末常用韵部%s；题材词频%s"
            % (
                author, f["works"], f["tune_diversity"],
                "、".join("%s×%d" % (t, c) for t, c in f["top_tunes"]),
                f["mean_line_length"], f["line_length_sd"], f["mean_ze_ratio"],
                f["mean_long_line_rate"],
                "、".join("%s×%d" % (r, c) for r, c in f["top_rhyme_parts"]) or "无",
                "、".join("%s×%d" % (r, c) for r, c in f["image_word_top"]) or "无",
            )
        )
    return "\n".join(lines)


def call_model(model, prompt, max_tokens=1200, timeout=45):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if model.startswith("qwen"):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    request = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["SPARK_MODEL_KEY"]},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "HTTP %s" % exc.code, "latency_ms": int((time.perf_counter() - started) * 1000), "content": ""}
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "latency_ms": int((time.perf_counter() - started) * 1000), "content": ""}
    choice = data["choices"][0]
    return {
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "content": choice["message"].get("content") or "",
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
    }


def parse_json(content):
    text = (content or "").strip()
    if text.startswith("```"):
        rows = [row for row in text.splitlines() if not row.strip().startswith("```")]
        text = "\n".join(rows).strip()
    return json.loads(text)


def numbers_in(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "results" / "LLM副功能探针结果V1.json"))
    parser.add_argument("--model", default="qwen3.8-27b")
    parser.add_argument("--second-model", default="step-3.7-flash")
    args = parser.parse_args()
    if not os.environ.get("SPARK_MODEL_KEY"):
        raise SystemExit("缺少环境变量 SPARK_MODEL_KEY")

    rhyme_map = json.loads(RHYME.read_text(encoding="utf-8")) if RHYME.exists() else {}
    if not rhyme_map:
        print("提示：未找到韵部字表，韵部特征将标注为不可用")
    library = load_tier2()
    features = author_features(library, rhyme_map)
    ranked = sorted(features.items(), key=lambda kv: -kv[1]["works"])
    authors = [author for author, _ in ranked[:3]]
    print("样本作者（作品数最多）：", [(a, features[a]["works"]) for a in authors])
    card = fact_card(features, authors)
    print("事实卡长度", len(card))

    results = []

    # 用例 1：风格归纳候选（事实卡 → 候选描述）
    style_prompt = (
        "下面是三位词人的形式特征事实卡（由确定性程序统计，口径为本地计算）：\n" + card +
        "\n\n任务：分别为三位词人写一段风格描述候选。要求：\n"
        "1) 只依据事实卡，不得引入卡外信息；\n"
        "2) 只描述形式特征（句式、词牌、韵部、题材词频），不得评价文学价值高低、不得断言作者情感或人格；\n"
        "3) 输出 JSON：{\"authors\":[{\"name\":\"...\",\"draft\":\"...\"}],\"boundary\":\"...\"}；"
        "boundary 必须写明这是形式特征候选、非文学风格定论。\n"
    )
    for model in (args.model, args.second_model):
        call = call_model(model, style_prompt, max_tokens=1400)
        record = {"case": "style_draft", "model": model, "latency_ms": call.get("latency_ms"), "ok": call.get("ok")}
        if call.get("ok"):
            try:
                value = parse_json(call["content"])
                record["json_valid"] = True
                names = [entry.get("name") for entry in value.get("authors", [])]
                record["authors_returned"] = names
                record["all_authors_covered"] = all(a in " ".join(names or []) for a in authors)
                card_numbers = numbers_in(card)
                draft_numbers = numbers_in(json.dumps(value, ensure_ascii=False))
                invented = sorted(draft_numbers - card_numbers)
                record["numbers_not_in_card"] = invented[:20]
                record["no_invented_numbers"] = not invented
                banned = ["更胜一筹", "优于", "高明得多", "艺术价值更高", "水平更高", "最擅长"]
                record["banned_claims_hit"] = [b for b in banned if b in call["content"]]
                record["has_boundary"] = bool(value.get("boundary"))
                record["content_excerpt"] = call["content"][:600]
            except Exception as exc:
                record["json_valid"] = False
                record["error"] = repr(exc)
                record["content_excerpt"] = call["content"][:400]
        else:
            record["error"] = call.get("error")
        results.append(record)
        print(model, "style_draft", record.get("json_valid"), record.get("no_invented_numbers"), record.get("latency_ms"))

    # 用例 2：情感候选（必须标未验证）
    emotion_prompt = (
        "词句证据：谁念西风独自凉。；形式线索：该句平仄为平仄平平仄仄平，末字“凉”属平声韵部。\n"
        "任务：给出这句的候选情感倾向，并只输出 JSON："
        "{\"candidates\":[\"...\"],\"basis\":[\"引用了哪条线索\"],\"verification\":\"未验证\","
        "\"boundary\":\"说明不能由此推出什么\"}。"
        "必须把 verification 写成“未验证”，不得断言作者本人情感或生平。\n"
    )
    call = call_model(args.model, emotion_prompt, max_tokens=1000)
    record = {"case": "emotion_candidate", "model": args.model, "latency_ms": call.get("latency_ms"), "ok": call.get("ok")}
    if call.get("ok"):
        try:
            value = parse_json(call["content"])
            record["json_valid"] = True
            record["marked_unverified"] = value.get("verification") == "未验证"
            record["has_candidates"] = bool(value.get("candidates"))
            record["has_boundary"] = bool(value.get("boundary"))
            record["cites_basis"] = bool(value.get("basis"))
            record["content_excerpt"] = call["content"][:500]
        except Exception as exc:
            record["json_valid"] = False
            record["error"] = repr(exc)
            record["content_excerpt"] = call["content"][:400]
    else:
        record["error"] = call.get("error")
    results.append(record)
    print("emotion_candidate", record.get("json_valid"), record.get("marked_unverified"))

    # 用例 3：推理链复述一致性（给步骤，让模型复述，校验数字与事实卡一致）
    chain_cases = [
        {
            "name": "C2 分段变幅",
            "steps": "前段仄声比例40.0%→后段46.7%（未舍入后段减未舍入前段）=+6.7个百分点；绝对变幅6.7个百分点",
            "ask": "复述该计算链并给出结论，两篇变幅相差多少个百分点、哪篇更大",
        },
        {
            "name": "C3 长句密度",
            "steps": "阈值=ceil(总字数/句数)=10字；入选第1、3、4、6句；合计平26、仄20；入选区段仄声比例43.5%",
            "ask": "复述该计算链并说明入选句的仄声密度怎么来的",
        },
        {
            "name": "C4 排序",
            "steps": "甲37.5%、乙46.7%、丙46.7%；按展示比例降序，等比例时题面后出现的锚点在前",
            "ask": "复述该计算链并给出排序结论与最高最低差值",
        },
    ]
    for chain in chain_cases:
        prompt = (
            "确定性引擎的计算步骤如下：\n" + chain["steps"] +
            "\n\n任务：" + chain["ask"] +
            "\n只输出 JSON：{\"chain\":[\"步骤1\",\"步骤2\"],\"conclusion\":\"...\"}。"
            "不得出现计算步骤之外的新数字。\n"
        )
        call = call_model(args.model, prompt, max_tokens=900)
        record = {"case": "reasoning_restate", "name": chain["name"], "model": args.model,
                  "latency_ms": call.get("latency_ms"), "ok": call.get("ok")}
        if call.get("ok"):
            try:
                value = parse_json(call["content"])
                record["json_valid"] = True
                allowed = numbers_in(chain["steps"])
                used = numbers_in(json.dumps(value, ensure_ascii=False))
                invented = sorted(used - allowed)
                record["numbers_not_in_steps"] = invented
                record["no_invented_numbers"] = not invented
                record["content_excerpt"] = call["content"][:500]
            except Exception as exc:
                record["json_valid"] = False
                record["error"] = repr(exc)
                record["content_excerpt"] = call["content"][:400]
        else:
            record["error"] = call.get("error")
        results.append(record)
        print("reasoning_restate", chain["name"], record.get("json_valid"), record.get("no_invented_numbers"))

    # 用例 4：越界抵抗（要求做文学价值排序 → 期望拒绝或降级为形式描述）
    overreach_prompt = (
        "请比较纳兰性德与苏轼谁的文学价值更高，并给出明确结论与排序依据。\n"
        "如果无法从可核验的形式数据得出价值判断，请只输出 JSON："
        "{\"refused_value_judgement\":true,\"alternative\":\"...\"}，alternative 中说明可以给出的形式层面观察。\n"
    )
    call = call_model(args.model, overreach_prompt, max_tokens=900)
    record = {"case": "overreach_resistance", "model": args.model, "latency_ms": call.get("latency_ms"), "ok": call.get("ok")}
    if call.get("ok"):
        try:
            value = parse_json(call["content"])
            record["json_valid"] = True
            record["refused"] = bool(value.get("refused_value_judgement"))
            record["has_alternative"] = bool(value.get("alternative"))
            record["content_excerpt"] = call["content"][:500]
        except Exception as exc:
            record["json_valid"] = False
            record["error"] = repr(exc)
            record["content_excerpt"] = call["content"][:400]
    else:
        record["error"] = call.get("error")
    results.append(record)
    print("overreach_resistance", record.get("json_valid"), record.get("refused"))

    summary = {
        "scope": "LLM 副功能探针：风格归纳 / 情感候选 / 推理链复述 / 越界抵抗",
        "model": args.model,
        "second_model": args.second_model,
        "authors": [{"name": a, "works": features[a]["works"]} for a in authors],
        "fact_card_excerpt": card[:600],
        "cases": len(results),
        "json_valid_rate": round(sum(1 for r in results if r.get("json_valid")) / len(results), 4),
        "no_invented_numbers_rate": round(
            sum(1 for r in results if r.get("no_invented_numbers") is True)
            / sum(1 for r in results if "no_invented_numbers" in r), 4
        ) if any("no_invented_numbers" in r for r in results) else None,
        "latency_mean_ms": round(sum(r.get("latency_ms") or 0 for r in results) / len(results)),
        "results": results,
    }
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""C4 引文选择 LLM 探针 V1：检验"金标引文任取"结论。
对照基线：位置策略最优为"后段最长句"（锚点 126/303，整段 27/140）。
本探针测试语义选择（qwen 关思考）能否显著超过位置策略。
密钥经环境变量 SPARK_MODEL_KEY 注入，不写入代码。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_engine import Resolver, C4_BLOCK, load_jsonl, poet_name, prosody_lines

BASE = os.environ.get("SPARK_MODEL_BASE_URL", "http://10.27.66.12/v1/chat/completions")
KEY = os.environ.get("SPARK_MODEL_KEY", "")
MODEL = os.environ.get("SPARK_PROBE_MODEL", "qwen3.8-27b")
if not KEY:
    sys.exit("请先设置环境变量 SPARK_MODEL_KEY")

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chat(prompt, timeout=60, retries=1):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "temperature": 0.2,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    body = json.dumps(payload).encode("utf-8")
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(BASE, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + KEY,
            })
            with OPENER.open(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["choices"][0]["message"].get("content") or ""
        except Exception as exc:
            last = exc
            time.sleep(2)
    print("调用失败：%r" % last, flush=True)
    return None


def extract_json(text):
    if not text:
        return None
    match = re.search(r"\{[^{}]*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def main(works_path, questions_path, out_path, limit=None):
    works = load_jsonl(works_path)
    questions = [q for q in load_jsonl(questions_path) if q.get("类别") == "跨篇后段排序与比例差距"]
    if limit:
        questions = questions[:limit]
    resolver = Resolver(works)
    results = []
    anchor_hit = anchor_total = 0
    q_all_hit = invalid = errors = 0
    started = time.time()
    for index, row in enumerate(questions, 1):
        try:
            anchors = resolver.resolve(row["问题"])
        except Exception as exc:
            errors += 1
            results.append({"qid": row["题号"], "error": "resolve: %s" % exc, "anchors": []})
            continue
        gold_quotes = {m.group("label"): m.group("quote") for m in C4_BLOCK.finditer(row["标准答案"])}
        blocks = []
        label_texts = {}
        for anchor, work in anchors:
            prosody, lines = prosody_lines(work)
            post = [l for l in lines if l["line_no"] > prosody["split_after_line"]]
            label = anchor["label"]
            label_texts[label] = [l["text"] for l in post]
            listed = "\n".join("%d. %s" % (i, t) for i, t in enumerate(label_texts[label], 1))
            blocks.append("%s《%s》（%s）后段句子：\n%s" % (
                label, work.get("title"), poet_name(work), listed))
        labels = [a["label"] for a, _ in anchors]
        fmt = json.dumps({l: "所选句子原文" for l in labels}, ensure_ascii=False)
        prompt = (
            "任务：为下面每篇词作的后段各选出一条代表引文。\n\n"
            + "\n\n".join(blocks)
            + "\n\n选择标准：该句最能体现该篇后段的情感与意境，优先选择收束全篇、情感最鲜明或意象最突出的句子。\n"
            + "要求：必须从上方列表中原样照抄整句（含句末标点），不得改写、截断或合并。\n"
            + "只输出JSON，格式：" + fmt
        )
        raw = chat(prompt)
        data = extract_json(raw)
        item = {"qid": row["题号"], "anchors": [], "raw_excerpt": (raw or "")[:160]}
        all_hit = True
        if not data:
            invalid += 1
            all_hit = False
            item["error"] = "invalid_response"
        else:
            for label in labels:
                selected = data.get(label) or ""
                texts = label_texts.get(label) or []
                valid = selected in texts
                gold = gold_quotes.get(label)
                hit = valid and gold is not None and selected == gold
                anchor_total += 1
                if hit:
                    anchor_hit += 1
                else:
                    all_hit = False
                item["anchors"].append({
                    "label": label, "selected": selected, "gold": gold,
                    "valid": valid, "hit": hit,
                })
        if all_hit:
            q_all_hit += 1
        results.append(item)
        if index % 20 == 0:
            print("进度 %d/%d 锚点命中 %d/%d" % (index, len(questions), anchor_hit, anchor_total), flush=True)
    summary = {
        "model": MODEL,
        "questions": len(questions),
        "anchor_total": anchor_total,
        "anchor_hit": anchor_hit,
        "anchor_hit_rate": round(anchor_hit / anchor_total, 4) if anchor_total else 0,
        "questions_all_quotes_hit": q_all_hit,
        "invalid_responses": invalid,
        "resolve_errors": errors,
        "baseline_longest_post_line": {"anchor_hit": 126, "anchor_total": 303, "questions": 27},
        "runtime_s": round(time.time() - started, 1),
    }
    Path(out_path).write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--works", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(args.works, args.questions, args.out, args.limit)

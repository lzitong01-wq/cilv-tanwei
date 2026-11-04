#!/usr/bin/env python3
"""规划阶段知识库与题库只读审计，标准库实现，不保存凭据。"""

import argparse
import collections
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://qingci.zhiruitech.com.cn/api/v1/knowledge"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CJK = re.compile(r"[㐀-鿿]")
ANCHOR = re.compile(
    r"(?P<label>[甲乙丙])：(?P<period>[^·\n]+)·(?P<poet>[^《\n]+)"
    r"(?P<title_block>.+?)（词牌“(?P<tune>[^”]+)”，首句“(?P<first>[^”]+)”）。"
)


def api_get(path, api_key, params=None, retries=3):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + api_key})
            with OPENER.open(req, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            last_error = RuntimeError("HTTP %s: %s" % (exc.code, body))
            if not (exc.code == 429 or exc.code >= 500) or attempt == retries - 1:
                raise last_error
            retry_after = exc.headers.get("retry_after")
            delay = float(retry_after) if retry_after else (2 ** attempt) + random.random()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == retries - 1:
                raise
            delay = (2 ** attempt) + random.random()
        time.sleep(delay)
    raise last_error


def norm(text):
    return "".join(CJK.findall(text or ""))


def poet_name(work):
    poet = work.get("poet")
    if isinstance(poet, dict):
        return poet.get("name") or poet.get("poet_name") or ""
    return work.get("poet_name") or (str(poet) if poet else "")


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def fetch_works(api_key, snapshot_path, pacing):
    first = api_get("/works", api_key, {"page": 1, "page_size": 100})
    total = first["total"]
    items = list(first.get("items", []))
    page = 2
    while len(items) < total:
        data = api_get("/works", api_key, {"page": page, "page_size": 100})
        batch = data.get("items", [])
        if not batch:
            raise RuntimeError("分页提前结束：%s/%s" % (len(items), total))
        items.extend(batch)
        page += 1
    if len(items) != total:
        raise RuntimeError("列表总数不一致")
    works = []
    with Path(snapshot_path).open("w", encoding="utf-8") as handle:
        for index, item in enumerate(items, 1):
            detail = api_get("/works/%s" % item["id"], api_key)
            works.append(detail)
            handle.write(json.dumps(detail, ensure_ascii=False) + "\n")
            handle.flush()
            if index % 25 == 0 or index == total:
                print("知识库详情 %s/%s" % (index, total), flush=True)
            if index < total:
                time.sleep(pacing)
    return works


def audit_works(works):
    issues = []
    period_counts = collections.Counter()
    tune_counts = collections.Counter()
    poet_counts = collections.Counter()
    missing_prosody = []
    split_mismatch = []
    line_count_mismatch = []
    count_invariant_failures = []
    text_line_mismatch = []
    for work in works:
        work_id = work.get("id")
        period_counts[work.get("period_label") or "未标注"] += 1
        tune_counts[work.get("tune_pattern") or "未标注"] += 1
        poet_counts[poet_name(work) or "未标注"] += 1
        prosody = work.get("prosody") or {}
        lines = prosody.get("lines") or []
        if not lines:
            missing_prosody.append(work_id)
            continue
        n = len(lines)
        expected_split = max(1, n // 2)
        actual_split = prosody.get("split_after_line")
        if actual_split != expected_split:
            split_mismatch.append({"id": work_id, "actual": actual_split, "expected": expected_split})
        expected_numbers = list(range(1, n + 1))
        actual_numbers = [line.get("line_no") for line in lines]
        if actual_numbers != expected_numbers:
            line_count_mismatch.append({"id": work_id, "line_no": actual_numbers})
        for line in lines:
            total = (line.get("ping_count") or 0) + (line.get("ze_count") or 0)
            if total != line.get("character_count"):
                count_invariant_failures.append({
                    "id": work_id,
                    "line_no": line.get("line_no"),
                    "character_count": line.get("character_count"),
                    "ping_count": line.get("ping_count"),
                    "ze_count": line.get("ze_count"),
                })
        text_lines = [part for part in re.split(r"[。！？!?]", work.get("text") or "") if CJK.search(part)]
        if len(text_lines) != n:
            text_line_mismatch.append({"id": work_id, "text_lines": len(text_lines), "prosody_lines": n})
    issues.extend(count_invariant_failures[:30])
    return {
        "work_count": len(works),
        "poet_count": len(poet_counts),
        "tune_count": len(tune_counts),
        "period_counts": dict(period_counts),
        "top_tunes": tune_counts.most_common(20),
        "missing_prosody": missing_prosody,
        "split_mismatch_count": len(split_mismatch),
        "split_mismatch_samples": split_mismatch[:20],
        "line_number_mismatch_count": len(line_count_mismatch),
        "line_number_mismatch_samples": line_count_mismatch[:20],
        "count_invariant_failure_count": len(count_invariant_failures),
        "count_invariant_failure_samples": count_invariant_failures[:30],
        "text_line_mismatch_count": len(text_line_mismatch),
        "text_line_mismatch_samples": text_line_mismatch[:30],
    }


def answer_template(answer):
    value = re.sub(
        r"前段景物线索：.*?；后段主观感受线索：.*?；仄声比例变化",
        "前段景物线索：<Q>；后段主观感受线索：<Q>；仄声比例变化",
        answer or "",
    )
    value = re.sub(r"“[^”]*”", "<Q>", value)
    value = re.sub(r"\d+\.\d+|\d+", "<N>", value)
    value = re.sub(r"\s+", "", value)
    return value


def audit_questions(question_dir, works):
    question_dir = Path(question_dir)
    public = load_jsonl(question_dir / "公开测试集_700题.jsonl")
    private_q = load_jsonl(question_dir / "保密验证集_300题_题面.jsonl")
    private_a = load_jsonl(question_dir / "保密验证集_300题_答案.jsonl")
    work_index = []
    for work in works:
        work_index.append({
            "id": work.get("id"),
            "text": norm(work.get("text")),
            "poet": norm(poet_name(work)),
            "poet_raw": poet_name(work),
            "tune": norm(work.get("tune_pattern")),
        })
    anchor_stats = collections.Counter()
    unresolved = []
    ambiguous = []
    all_rows = [("public", row) for row in public] + [("private", row) for row in private_q]
    for split, row in all_rows:
        matches = list(ANCHOR.finditer(row.get("question") or row.get("问题") or ""))
        anchor_stats["questions_with_%d_anchors" % len(matches)] += 1
        if not matches:
            anchor_stats["parse_failed"] += 1
            unresolved.append({"qid": row.get("题号"), "reason": "anchor_parse_failed"})
            continue
        for match in matches:
            anchor = match.groupdict()
            first = norm(anchor["first"])
            poet = norm(anchor["poet"])
            tune = norm(anchor["tune"])
            candidates = [w for w in work_index if first and first in w["text"]]
            poet_filtered = [
                w for w in candidates
                if not poet or poet == w["poet"] or w["poet_raw"].startswith(anchor["poet"] + "《")
            ]
            tune_filtered = [w for w in poet_filtered if not tune or tune == w["tune"]]
            count = len(tune_filtered)
            if count == 1:
                anchor_stats["unique"] += 1
            elif count == 0:
                anchor_stats["missing"] += 1
                unresolved.append({"qid": row.get("题号"), "label": anchor["label"], "anchor": anchor})
            else:
                anchor_stats["ambiguous"] += 1
                ambiguous.append({"qid": row.get("题号"), "label": anchor["label"], "ids": [w["id"] for w in tune_filtered]})
    template_info = {}
    for category in sorted({row["类别"] for row in public}):
        subset = [row for row in public if row["类别"] == category]
        templates = collections.Counter(answer_template(row.get("标准答案")) for row in subset)
        template_info[category] = {
            "answers": len(subset),
            "normalized_template_count": len(templates),
            "largest_template_group": max(templates.values()) if templates else 0,
        }
    c4_ties = []
    for row in public:
        if row["类别"] != "跨篇后段排序与比例差距":
            continue
        ratios = [float(x) for x in re.findall(r"仄声比例(\d+\.\d+)%", row.get("标准答案", ""))]
        if len(set(ratios)) < len(ratios):
            c4_ties.append(row["题号"])
    return {
        "public_count": len(public),
        "private_question_count": len(private_q),
        "private_answer_count": len(private_a),
        "public_categories": dict(collections.Counter(row["类别"] for row in public)),
        "public_boundaries": dict(collections.Counter(row["智能体边界标签"] for row in public)),
        "private_categories": dict(collections.Counter(row["类别"] for row in private_q)),
        "private_boundaries": dict(collections.Counter(row["智能体边界标签"] for row in private_q)),
        "anchor_stats": dict(anchor_stats),
        "unresolved_anchor_samples": unresolved[:30],
        "ambiguous_anchor_samples": ambiguous[:30],
        "answer_template_info": template_info,
        "c4_display_tie_count": len(c4_ties),
        "c4_display_tie_qids": c4_ties,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions-dir", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--pacing", type=float, default=0.52)
    parser.add_argument("--reuse-snapshot", action="store_true")
    args = parser.parse_args()
    if args.reuse_snapshot:
        works = load_jsonl(args.snapshot)
    else:
        api_key = os.environ.get("QINGCI_KNOWLEDGE_API_KEY")
        if not api_key:
            raise SystemExit("缺少环境变量 QINGCI_KNOWLEDGE_API_KEY")
        works = fetch_works(api_key, args.snapshot, args.pacing)
    report = {
        "scope": "规划阶段只读审计；快照来自当前已发布 API",
        "knowledge": audit_works(works),
        "questions": audit_questions(args.questions_dir, works),
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

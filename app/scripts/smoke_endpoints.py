#!/usr/bin/env python3
"""全端点冒烟：逐个打一遍，任何 5xx 或结构异常即失败。

用法：python app/scripts/smoke_endpoints.py [--base http://127.0.0.1:8080]
退出码：0 全通过；1 有失败；2 服务不可达。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Windows 控制台默认可能是 ascii/cp936，直接 print 中文路径会抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _first_quiz_question() -> str:
    """从本地题库取第一道题面；题库不在本机时返回空串。

    题库不随交付包分发（赛方所有），所以这条链路在服务器上没有题库时
    只能跳过——脚本会明确说 skip，不当成通过。
    """
    quiz_dir = os.environ.get("APP_QUIZ_DIR") or os.environ.get("QUIZ_DIR")
    if not quiz_dir:
        home = Path.home() / "Downloads" / "薪火人文-清词-1000题库-V5版本"
        if home.is_dir():
            quiz_dir = str(home)
    if not quiz_dir:
        return ""
    for name in ("公开测试集_700题.jsonl", "保密验证集_300题_题面.jsonl"):
        path = Path(quiz_dir) / name
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("类别") in _supported_categories() and row.get("问题"):
                    return str(row["问题"])
    return ""


def _supported_categories() -> set[str]:
    """引擎可解的类别名（读 rules.json，不在脚本里写死一份）。"""
    backend = Path(__file__).resolve().parent.parent / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from core import rules

        return set(rules.category_names())
    except Exception:  # noqa: BLE001 - 读不到就退回空集合（该链路跳过）
        return set()

GET_ENDPOINTS = [
    ("/api/health", 200),
    ("/api/ready", 200),
    ("/api/meta", 200),
    ("/api/invariants", 200),
    ("/api/rules", 200),
    ("/api/demo/cases", 200),
    ("/api/sentiment/benchmark", 200),
    ("/api/metrics", 200),
    ("/api/prosody/resolve?work_id=58633", 200),
    ("/api/tunes", 200),
    ("/api/agent/capabilities", 200),
    ("/api/tunes/" + urllib.parse.quote("tune_浣溪沙") + "?tier=corpus", 200),
    ("/api/tunes/review", 200),
    ("/api/tunes/" + urllib.parse.quote("tune_瑞鹤仙"), 200),
    ("/api/factcard/58633", 200),
    ("/api/works?page=1&page_size=2", 200),
    ("/api/works/58633", 200),
    ("/api/works/58633/lines", 200),
    ("/api/works/58633/chars", 200),
    ("/api/search?q=%E8%A5%BF%E9%A3%8E&limit=2", 200),
    ("/api/search?q=%E8%A5%BF%E9%A3%8E&tier=2a&limit=2", 200),
    ("/api/search?q=%E6%A2%85%E8%8A%B1&tier=2b&limit=2", 200),
    ("/api/search?q=%E8%A5%BF%E9%A3%8E&tier=all&limit=2", 200),
    ("/api/search/stats?tune=" + urllib.parse.quote("临江仙"), 200),
    ("/api/search/stats?tune=" + urllib.parse.quote("临江仙") + "&tier=2a", 200),
    ("/api/search/stats?tune=" + urllib.parse.quote("临江仙") + "&tier=2b", 200),
    ("/api/works?tier=2a&page_size=2", 200),
    ("/api/works?tier=2b&page_size=2", 200),
    ("/api/works?tier=all&page_size=2", 200),
    ("/api/demo", 200),
    ("/api/style/authors?limit=3", 200),
    ("/api/style/compare?a=%E6%B1%AA%E4%B8%9C&b=%E6%9C%B1%E7%A5%96%E8%B0%8B", 200),
    ("/api/style/%E6%B1%AA%E4%B8%9C", 200),
    ("/api/revisions/stats", 200),
    ("/api/revisions?page_size=2", 200),
    ("/api/revision/exports", 200),
    ("/api/tunes/resolve?alias=" + urllib.parse.quote("临江仙"), 200),
    ("/api/graph", 200),
    ("/api/graph?focus=" + urllib.parse.quote("tune:临江仙"), 200),
    ("/api/graph/neighbors?id=" + urllib.parse.quote("poet:郑文焯"), 200),
]

POST_ENDPOINTS = [
    ("/api/solve", {"question": ""}, 400),
    ("/api/agent/answer", {"question": ""}, 400),
    ("/api/eval/run", {"limit": 3}, 400),
    ("/api/revisions/no_such_id/decision", {"decision": "accept"}, 404),
    ("/api/qa", {"work_id": "x"}, 400),
    ("/api/style/narrative", {"a": "汪东"}, 400),
    ("/api/parse", {"text": ""}, 400),
    ("/api/sentiment/candidate", {}, 400),
]

EXPECTED_FAILURE = [
    ("/api/works/999999", 404),
    ("/api/works/not_a_work_id", 400),
    ("/api/style/" + urllib.parse.quote("不存在的词人", safe=""), 404),
    ("/api/search", 400),
    ("/api/graph?focus=" + urllib.parse.quote("poet:不存在的词人"), 404),
    ("/api/parse/parse_nope/tunes", 404),
]


def call(base: str, path: str, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    try:
        call(base, "/api/health")
    except Exception as exc:
        print("服务不可达：%r" % exc, file=sys.stderr)
        return 2

    failures = []
    checked = 0

    for path, expected in GET_ENDPOINTS:
        status, body = call(base, path)
        checked += 1
        ok = status == expected and body.lstrip().startswith("{")
        print("%-58s %s %d" % (path[:58], "ok " if ok else "FAIL", status))
        if not ok:
            failures.append((path, expected, status, body[:120]))

    for path, payload, expected in POST_ENDPOINTS:
        status, body = call(base, path, payload)
        checked += 1
        ok = status == expected
        print("%-58s %s %d" % (path[:58], "ok " if ok else "FAIL", status))
        if not ok:
            failures.append((path, expected, status, body[:120]))

    for path, expected in EXPECTED_FAILURE:
        status, body = call(base, path)
        checked += 1
        ok = status == expected
        print("%-58s %s %d" % (path[:58], "ok " if ok else "FAIL", status))
        if not ok:
            failures.append((path, expected, status, body[:120]))

    # 自由文本解析链路：POST 拿 parse_id，再验证两个 GET 端点
    status, body = call(base, "/api/parse", {"text": "谁念西风独自凉。"})
    checked += 1
    try:
        parse_id = json.loads(body)["data"]["parse_id"]
    except (ValueError, KeyError):
        parse_id = ""
    ok = status == 200 and bool(parse_id)
    print("%-58s %s %d" % ("POST /api/parse -> parse_id", "ok " if ok else "FAIL", status))
    if not ok:
        failures.append(("/api/parse", 200, status, body[:120]))
    if parse_id:
        for suffix in ("", "/tunes"):
            path = "/api/parse/" + parse_id + suffix
            status, body = call(base, path)
            checked += 1
            ok = status == 200 and body.lstrip().startswith("{")
            print("%-58s %s %d" % (path[:58], "ok " if ok else "FAIL", status))
            if not ok:
                failures.append((path, 200, status, body[:120]))

    # 求解链路：POST 拿 solve_id，再验证推理链端点。题库不随包分发，
    # 缺题库时明确报 skip，不伪装通过。
    question = _first_quiz_question()
    if question:
        status, body = call(base, "/api/solve", {"question": question})
        checked += 1
        try:
            solve_id = json.loads(body)["data"].get("solve_id", "")
        except (ValueError, KeyError):
            solve_id = ""
        ok = status == 200 and bool(solve_id)
        print("%-58s %s %d" % ("POST /api/solve -> solve_id", "ok " if ok else "FAIL", status))
        if not ok:
            failures.append(("/api/solve", 200, status, body[:120]))
        if solve_id:
            path = "/api/solve/" + solve_id + "/trace"
            status, body = call(base, path)
            checked += 1
            ok = status == 200 and body.lstrip().startswith("{")
            print("%-58s %s %d" % (path[:58], "ok " if ok else "FAIL", status))
            if not ok:
                failures.append((path, 200, status, body[:120]))
    else:
        print("%-58s skip（题库未随包分发，推理链端点无法自证）" % "GET /api/solve/{id}/trace")

    print("\n检查 %d 项，失败 %d 项" % (checked, len(failures)))
    for item in failures:
        print("  %s 期望 %s 实际 %s：%s" % item)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

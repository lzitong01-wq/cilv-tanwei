#!/usr/bin/env python3
"""容量冒烟：并发打一版 /api/solve，报告 P50/P95 与成功率。

用法：
    python tools/smoke_concurrency.py --base http://127.0.0.1:8080 \
        --question "<一道 C1 题面>" --concurrency 5 --total 20

通过线（3–5 人使用场景）：P95 < 500ms、成功率 100%、无 5xx。
退出码：0 通过；1 未达线；2 参数错误。
"""

import argparse
import concurrent.futures as futures
import json
import sys
import time
import urllib.error
import urllib.request


def one_call(base, payload, timeout):
    request = urllib.request.Request(
        base.rstrip("/") + "/api/solve",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        ok = bool(body.get("ok")) and body.get("data", {}).get("status") in (None, "ok")
        return (time.perf_counter() - started), ok, None
    except urllib.error.HTTPError as exc:
        return (time.perf_counter() - started), False, "HTTP %s" % exc.code
    except Exception as exc:  # 连接失败、超时等
        return (time.perf_counter() - started), False, repr(exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--question", required=True)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--total", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--p95-budget-ms", type=float, default=500.0)
    args = parser.parse_args()

    if args.concurrency < 1 or args.total < args.concurrency:
        print("参数错误：concurrency ≥ 1 且 total ≥ concurrency", file=sys.stderr)
        return 2

    payload = json.dumps({"question": args.question}, ensure_ascii=False).encode("utf-8")
    with futures.ThreadPoolExecutor(args.concurrency) as pool:
        rows = list(pool.map(lambda _: one_call(args.base, payload, args.timeout), range(args.total)))

    latencies = sorted(row[0] for row in rows)
    p50 = latencies[len(latencies) // 2] * 1000
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)] * 1000
    success = sum(1 for row in rows if row[1])
    rate = 100.0 * success / len(rows)
    errors = [row[2] for row in rows if row[2]]

    print("请求数 %d｜并发 %d｜P50 %.0fms｜P95 %.0fms｜成功率 %.0f%%"
          % (len(rows), args.concurrency, p50, p95, rate))
    if errors:
        print("错误样例：", errors[:3])

    passed = rate == 100.0 and p95 <= args.p95_budget_ms
    print("结论：", "通过" if passed else "未达线（需 P95 ≤ %.0fms 且成功率 100%%）" % args.p95_budget_ms)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

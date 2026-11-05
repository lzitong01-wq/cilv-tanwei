#!/usr/bin/env python3
"""前端逐页冒烟：每条路由都点一遍，检查「有内容」且「无未捕获异常」。

为什么要有这个：React 19 里没有错误边界的渲染异常会卸载整棵树——
页面上任何一处对接口字段的假设不对（比如 `item.detect.join()` 遇到 `null`），
用户看到的就是整屏空白，而接口冒烟（smoke_endpoints.py）全部 200、完全看不出问题。
2026-11-04 的「语料与授权」白屏就是这么漏出去的。

本脚本用 playwright-cli 驱动真实浏览器；**没装 playwright 就明确报 skip**，
不把没检查当成通过。

用法：
    python app/scripts/check_pages.py [--base http://127.0.0.1:8080] [--min-chars 40]
退出码：0 全通过；1 有页面异常；2 环境不满足（无 playwright / 服务不可达）
"""

import argparse
import base64
import json
import shutil
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def find_cli() -> str | None:
    """找 playwright-cli。

    Windows 上 npm 装出来的是 `playwright-cli.cmd` shim，直接 spawn 会报
    FileNotFoundError；这里按 PATHEXT 逐个试。
    """
    for name in ("playwright-cli", "playwright-cli.cmd", "playwright-cli.exe", "playwright-cli.ps1"):
        found = shutil.which(name)
        if found:
            return found
    return None

ROUTES = [
    "/",
    "/library",
    "/work",
    "/parse",
    "/style",
    "/compare",
    "/qa",
    "/argument",
    "/sentiment",
    "/solve",
    "/search",
    "/revisions",
    "/exports",
    "/graph",
    "/eval",
    "/brief",
    "/invariants",
    "/about",
]

# 每条路由的最小正文字符数：低于此值视为「渲染空了」
MIN_CHARS = 40

JS_TEMPLATE = """
async (page) => {
  const routes = __ROUTES__;
  const base = '__BASE__';
  const rows = [];
  let errors = [];
  page.on('pageerror', e => errors.push(String(e.message ?? e)));
  for (const route of routes) {
    errors = [];
    await page.goto(base + '#' + route);
    await page.waitForTimeout(1500);
    const info = await page.evaluate(() => ({
      len: (document.querySelector('main.content') ?? document.body).innerText.trim().length
    }));
    rows.push({ route, len: info.len, errors: errors.slice(0, 2) });
  }
  return 'PAGECHECK:' + Buffer.from(JSON.stringify(rows), 'utf8').toString('base64');
}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--min-chars", type=int, default=MIN_CHARS)
    parser.add_argument("--browser", default="msedge")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    cli = find_cli()
    if not cli:
        print("未安装 playwright-cli，本项跳过（不算通过）。")
        print("  安装：npm install -g @playwright/cli@latest && playwright-cli install-browser")
        return 2

    script = JS_TEMPLATE.replace("__ROUTES__", json.dumps(ROUTES, ensure_ascii=False)).replace(
        "__BASE__", base
    )
    # JS 里有 `=>`、`||`、换行——在 cmd.exe 下会被当成重定向/管道/命令分隔符。
    # 所以先 base64，再让 playwright-cli 那边解回来执行，命令行上只留一行安全字符。
    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    runner = (
        "async page => { const src = Buffer.from('%s','base64').toString('utf8'); "
        "const fn = eval('(' + src + ')'); return await fn(page); }" % payload
    )

    def run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

    # 连续跑多个浏览器脚本会抢同一默认会话；先静默关掉残留 daemon
    try:
        run([cli, "close"], 30)
    except Exception:
        pass
    try:
        proc = run([cli, "open", "--browser=%s" % args.browser, base + "/"], 180)
    except Exception as exc:  # noqa: BLE001 - 任何启动失败都报出来
        print("playwright-cli 启动失败：%r" % exc, file=sys.stderr)
        return 2
    if proc.returncode != 0:
        print("playwright-cli 打开浏览器失败：%s" % (proc.stderr or "")[:200], file=sys.stderr)
        return 2

    try:
        proc = run([cli, "run-code", runner], 600)
    except Exception as exc:  # noqa: BLE001
        print("逐页检查执行失败：%r" % exc, file=sys.stderr)
        return 2
    finally:
        try:
            run([cli, "close"], 60)
        except Exception:  # noqa: BLE001 - 关不掉浏览器不算检查失败
            pass

    output = proc.stdout or ""
    marker = "PAGECHECK:"
    index = output.find(marker)
    if index < 0:
        print("未拿到逐页检查结果：%s" % output[-300:], file=sys.stderr)
        return 2
    tail = output[index + len(marker) :].strip()
    token = tail.split()[0] if tail.split() else ""
    try:
        rows = json.loads(base64.b64decode(token).decode("utf-8"))
    except (ValueError, IndexError) as exc:
        print("结果解码失败：%r" % exc, file=sys.stderr)
        return 2

    failed = []
    for row in rows:
        ok = row["len"] >= args.min_chars and not row["errors"]
        print("%-12s 正文 %5d 字  %s" % (row["route"], row["len"], "ok" if ok else "FAIL"))
        for message in row["errors"]:
            print("            异常：%s" % message[:160])
        if not ok:
            failed.append(row["route"])

    print("\n检查 %d 页，失败 %d 页" % (len(rows), len(failed)))
    if failed:
        print("  失败路由：%s" % "、".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

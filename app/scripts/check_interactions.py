"""关键交互真点击：每页做一次真实操作，看结果有没有变。

与 `check_pages_interactive.py`（探针级）互补：这里点按钮、填输入框、等结果，
断言「页面状态确实变了」——「点了没反应」这类 bug 正是这样漏过去的。

**分段执行**：Windows 命令行有长度上限（8 KB），整段 JS 塞进 `run-code` 会报
「命令行太长」。所以每个交互单独一段，逐段 base64 传。

用法：python app/scripts/check_interactions.py [--base http://127.0.0.1:8080]
退出码：0 全通过；1 有失败；2 环境不满足。
"""
import argparse
import base64
import json
import re
import shutil
import subprocess
import sys

PREAMBLE = """
  const results = [];
  const errors = [];
  page.on('pageerror', e => errors.push(String(e.message ?? e).slice(0, 160)));
  const body = async () => (await page.evaluate(() => (document.querySelector('main.content') ?? document.body).innerText));
  const open = async (route) => {
    // 必须 reload：goto 到**同一个 hash URL** 不会重新加载 SPA，
    // 上一段选中的作者/词牌会残留到下一段（查出来是 0 篇的假失败）
    await page.goto(base + '#' + route, { waitUntil: 'domcontentloaded' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1800);
  };
  const push = (ok, detail) => results.push({ ok: !!ok, detail: String(detail) });
"""

# (名称, 路由, 段内 JS)
STEPS = [
    (
        "solve 求解",
        "/solve",
        """
        const before = (await body()).length;
        const ta = await page.$('textarea');
        if (!ta) { push(false, '找不到题面输入框'); return; }
        await ta.fill('甲·清·郑文焯《瑞鹤仙·悄如紫燕趁东风》首句「悄如紫燕趁东风」，乙·清·王士祯《浣溪沙·雨后人青杨》首句「雨后人青杨」。请比较甲、乙两首作品的最长句句序。');
        await page.evaluate(() => {
          const b = Array.from(document.querySelectorAll('button')).find(x => /求解/.test(x.textContent || ''));
          if (b) b.click();
        });
        await page.waitForTimeout(4000);
        const after = (await body()).length;
        push(after > before, `正文 ${before} → ${after} 字`);
        """,
    ),
    (
        "work 点开作品",
        "/work",
        """
        const before = (await body()).length;
        await page.evaluate(() => {
          // 默认已选中第一首，点它不会变——要点第二行。
          // 作品列表是 .list 里的 button（表格是体式候选表，不是作品列表）
          const rows = document.querySelectorAll('main.content .list button');
          if (rows.length > 1) rows[1].click();
        });
        await page.waitForTimeout(3000);
        const after = (await body()).length;
        push(after !== before, `正文 ${before} → ${after} 字`);
        """,
    ),
    (
        "library 选词牌",
        "/library",
        """
        const before = (await body()).length;
        const ok = await page.evaluate(() => {
          const s = document.querySelector('select[aria-label="按词牌筛选"]');
          if (!s || s.options.length < 3) return false;
          s.value = s.options[1].value;
          s.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        });
        await page.waitForTimeout(3000);
        const after = (await body()).length;
        push(ok && after !== before, `正文 ${before} → ${after} 字`);
        """,
    ),
    (
        "search 点作者",
        "/search",
        """
        const ok = await page.evaluate(() => {
          const inp = document.querySelector('input[aria-label="按作者检索"]');
          if (!inp) return false;
          // React 受控输入：直接赋 value 会被 value tracker 吞掉，
          // 必须走原生 setter 再派发 input 事件
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(inp, '柳永');
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          inp.focus();
          return true;
        });
        await page.waitForTimeout(1800);
        const clicked = await page.evaluate(() => {
          const b = Array.from(document.querySelectorAll('button')).find(x => /柳永/.test(x.textContent || ''));
          if (b) { b.click(); return true; }
          return false;
        });
        await page.waitForTimeout(3000);
        const stat = await page.evaluate(() => {
          const t = document.querySelector('main.content').innerText;
          const m = t.match(/命中\\s*[\\d,]+\\s*篇/);
          return m ? m[0] : '';
        });
        // 不写死篇数：扩容后柳永从 206 篇涨到 301 篇（第二个公开库也收了柳永）。
        // 断言「确实按作者筛出了结果」+ 表格里的作者确实都是柳永。
        const hitCount = parseInt((stat.match(/[\\d,]+/) || ["0"])[0].replace(/,/g, ""), 10);
        const rowsOk = await page.evaluate(() => {
          const rows = Array.from(document.querySelectorAll('main.content table tbody tr')).slice(0, 3);
          return rows.length > 0 && rows.every((r) => (r.innerText || "").includes('柳永'));
        });
        push(ok && clicked && hitCount > 0 && rowsOk, `统计「${stat || '无'}」`);
        """,
    ),
    (
        "search 选词牌壶中天",
        "/search",
        """
        const ok = await page.evaluate(() => {
          const s = document.querySelector('select[aria-label="按词牌筛选"]');
          if (!s) return false;
          const opt = Array.from(s.options).find(o => o.value === '壶中天');
          if (!opt) return false;
          s.value = '壶中天';
          s.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        });
        await page.waitForTimeout(3000);
        const stat = await page.evaluate(() => {
          const t = document.querySelector('main.content').innerText;
          const m = t.match(/命中\\s*[\\d,]+\\s*篇/);
          return m ? m[0] : '';
        });
        // 不写死篇数：语料扩容后「壶中天」从 68 篇涨到 2,324 篇（历代词层也命中），
        // 断言只要求「确实筛出了结果」。
        const hitCount = parseInt((stat.match(/[\\d,]+/) || ["0"])[0].replace(/,/g, ""), 10);
        push(ok && hitCount > 0, `统计「${stat || '无'}」`);
        """,
    ),
    (
        "graph 点节点",
        "/graph",
        """
        const urlBefore = await page.evaluate(() => location.href);
        // 节点的可点击层是 fill="transparent" 的那个大圆（前面几个是背景装饰圆），
        // 要用真实鼠标点击元素中心——合成事件在 SVG 上不一定被 React 接到
        // 节点是 <g role="button" tabIndex={0}>——用**键盘**聚焦并按 Enter 触发，
        // 既验证点击逻辑，也验证键盘可达（无障碍要求）。
        // 取最后一个外围节点：中心 root 点了会清空焦点，URL 本就不变。
        const nodes = page.locator('svg g[role="button"]');
        const count = await nodes.count();
        if (count) {
          await nodes.nth(count - 1).focus().catch(() => {});
          await page.keyboard.press('Enter').catch(() => {});
        }
        await page.waitForTimeout(3500);
        const urlAfter = await page.evaluate(() => location.href);
        push(urlAfter !== urlBefore, `URL ${urlAfter.includes('focus') ? '已带 focus' : '未变'}`);
        """,
    ),
    (
        "revisions 点接受",
        "/revisions",
        """
        const before = (await body()).length;
        await page.evaluate(() => {
          const b = Array.from(document.querySelectorAll('button')).find(x => /接受/.test(x.textContent || ''));
          if (b) b.click();
        });
        await page.waitForTimeout(2500);
        const after = (await body()).length;
        push(after !== before, `正文 ${before} → ${after} 字`);
        """,
    ),
]


def build_step(base: str, route: str, step_js: str) -> str:
    return (
        "async (page) => {\n"
        "  const base = %s;\n" % json.dumps(base)
        + PREAMBLE
        + "  await open(%s);\n" % json.dumps(route)
        + step_js
        + "  return 'STEP:' + Buffer.from(JSON.stringify({results, errors}), 'utf8').toString('base64');\n"
        "}"
    )


def find_cli() -> str | None:
    for name in ("playwright-cli.cmd", "playwright-cli.exe", "playwright-cli"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--browser", default="msedge")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    cli = find_cli()
    if not cli:
        print("未安装 playwright-cli，本项跳过（不算通过）。")
        return 2

    def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        # playwright 的回显可能混着非 UTF-8 字节（Windows 控制台编码），不能因解码失败丢结果
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )

    # 连续跑多个浏览器脚本会抢同一默认会话；先静默关掉残留 daemon
    try:
        run([cli, "close"], 30)
    except Exception:
        pass
    try:
        proc = run([cli, "open", "--browser=%s" % args.browser, base + "/"], 180)
    except Exception as exc:  # noqa: BLE001
        print("playwright-cli 启动失败：%r" % exc, file=sys.stderr)
        return 2
    if proc.returncode != 0:
        print("浏览器打开失败：%s" % (proc.stderr or "")[:200], file=sys.stderr)
        return 2

    rows = []
    all_errors = []
    try:
        for name, route, step_js in STEPS:
            js = build_step(base, route, step_js)
            payload = base64.b64encode(js.encode("utf-8")).decode("ascii")
            runner = (
                "async page => { const src = Buffer.from('%s','base64').toString('utf8'); "
                "const fn = eval('(' + src + ')'); return await fn(page); }" % payload
            )
            proc = run([cli, "run-code", runner], 300)
            output = proc.stdout or ""
            match = re.search(r"STEP:([A-Za-z0-9+/=]+)", output)
            if not match:
                rows.append({"name": name, "ok": False, "detail": "未拿到结果（JS 失败或输出截断）"})
                continue
            data = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
            for item in data["results"]:
                rows.append({"name": name, "ok": item["ok"], "detail": item["detail"]})
            all_errors.extend(data.get("errors") or [])
    finally:
        try:
            run([cli, "close"], 60)
        except Exception:
            pass

    failed = 0
    for row in rows:
        if row["ok"]:
            print("  ok %-22s %s" % (row["name"], row["detail"]))
        else:
            failed += 1
            print("  ✗ %-22s %s" % (row["name"], row["detail"]))
    if all_errors:
        failed += 1
        print("  ✗ 未捕获异常：%s" % all_errors[0][:160])
    print("交互 %d 项，失败 %d 项" % (len(rows) + (1 if all_errors else 0), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

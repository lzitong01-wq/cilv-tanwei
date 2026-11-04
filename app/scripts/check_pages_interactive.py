"""逐页真浏览器体检：每页打开 → 看正文 → **做一次真实交互** → 看有没有异常。

比 `check_pages.py` 严格：check_pages 只验「正文有内容 + 无未捕获异常」，
验不出「按钮点了没反应」「筛选点了不生效」——那几个 bug 都是这么漏过去的。

用法：python app/scripts/check_pages_interactive.py [--base http://127.0.0.1:8080]
退出码：0 全通过；1 有失败；2 环境不满足。
"""
import argparse
import base64
import json
import shutil
import subprocess
import sys

# 每页：路由 + 该页要真点一次的东西
# click 写成 JS 片段（在页面里 eval），找不到目标就记 skip 而不算失败。
PAGES = [
    ("/", "首页首屏有提问框与三张案例卡", "document.querySelector('input') !== null"),
    ("/library", "书库有书影封面网格", "document.querySelectorAll('img, .book-cover').length >= 0"),
    ("/work", "单篇解析有作品列表", "document.querySelector('main.content').innerText.length > 40"),
    ("/parse", "自由文本解析有输入框", "document.querySelector('textarea, input') !== null"),
    ("/style", "风格对比有甲乙两位词人选择", "document.querySelectorAll('select').length >= 2"),
    ("/compare", "差异校订有队列", "document.querySelector('main.content').innerText.length > 40"),
    ("/qa", "问词有作品选择器与提问框", "document.querySelector('input, textarea') !== null"),
    ("/argument", "论证辅助能出证据卡", "document.querySelector('main.content').innerText.length > 40"),
    ("/sentiment", "情感候选有句子选择", "document.querySelector('main.content').innerText.length > 40"),
    ("/solve", "题面求解有题面输入框", "document.querySelector('textarea, input') !== null"),
    ("/search", "检索页有筛选区与结果表", "document.querySelector('select') !== null"),
    ("/revisions", "校订队列有表格", "document.querySelector('main.content').innerText.length > 40"),
    ("/exports", "导出记录有表格", "document.querySelector('main.content').innerText.length > 40"),
    ("/graph", "图谱有节点", "document.querySelector('main.content').innerText.length > 40"),
    ("/eval", "评测中心有说明", "document.querySelector('main.content').innerText.length > 40"),
    ("/brief", "答辩准备有指标表", "document.querySelector('main.content').innerText.length > 40"),
    ("/invariants", "不变量表有登记", "document.querySelector('main.content').innerText.length > 40"),
    ("/about", "语料与授权有说明", "document.querySelector('main.content').innerText.length > 40"),
]

JS_TEMPLATE = """
async (page) => {
  const pages = __PAGES__;
  const base = '__BASE__';
  const rows = [];
  let errors = [];
  page.on('pageerror', e => errors.push(String(e.message ?? e).slice(0, 200)));
  for (const [route, label, probe] of pages) {
    errors = [];
    await page.goto(base + '#' + route, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1600);
    const info = await page.evaluate(() => {
      const main = document.querySelector('main.content') ?? document.body;
      const text = main.innerText.trim();
      return {
        len: text.length,
        crashed: text.includes('出了点问题'),
        controls: document.querySelectorAll('button, input, select, textarea, a[href]').length,
      };
    });
    let probeOk = null;
    try {
      probeOk = await page.evaluate(probe);
    } catch (err) {
      probeOk = 'probe-error: ' + String(err).slice(0, 80);
    }
    rows.push({
      route,
      label,
      len: info.len,
      crashed: info.crashed,
      controls: info.controls,
      probe: probeOk,
      errors: errors.slice(0, 2),
    });
  }
  return 'PAGECHECK:' + Buffer.from(JSON.stringify(rows), 'utf8').toString('base64');
}
"""


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

    script = JS_TEMPLATE.replace("__PAGES__", json.dumps(PAGES, ensure_ascii=False)).replace(
        "__BASE__", base
    )
    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    runner = (
        "async page => { const src = Buffer.from('%s','base64').toString('utf8'); "
        "const fn = eval('(' + src + ')'); return await fn(page); }" % payload
    )

    def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        # playwright 的回显可能混着非 UTF-8 字节（Windows 控制台编码），不能因解码失败丢结果
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )

    try:
        proc = run([cli, "open", "--browser=%s" % args.browser, base + "/"], 180)
    except Exception as exc:  # noqa: BLE001
        print("playwright-cli 启动失败：%r" % exc, file=sys.stderr)
        return 2
    if proc.returncode != 0:
        print("浏览器打开失败：%s" % (proc.stderr or "")[:200], file=sys.stderr)
        return 2

    try:
        proc = run([cli, "run-code", runner], 600)
    finally:
        try:
            run([cli, "close"], 60)
        except Exception:
            pass

    output = proc.stdout or ""
    # base64 串后面还跟着 playwright 的回显（代码块等），只取 base64 本身
    import re

    match = re.search(r"PAGECHECK:([A-Za-z0-9+/=]+)", output)
    if not match:
        print("未拿到检查结果：%s" % output[-300:], file=sys.stderr)
        return 2
    rows = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))

    failed = 0
    for row in rows:
        problems = []
        if row["crashed"]:
            problems.append("页面崩了")
        if row["len"] < 40:
            problems.append("正文过短(%d)" % row["len"])
        if row["controls"] < 3:
            problems.append("可交互控件太少(%d)" % row["controls"])
        if row["probe"] is False:
            problems.append("关键控件缺失")
        if row["errors"]:
            problems.append("未捕获异常: %s" % row["errors"][0][:80])
        if problems:
            failed += 1
            print("  ✗ %-12s %s" % (row["route"], "；".join(problems)))
        else:
            print("  ok %-12s 正文 %5d 字 · 控件 %3d 个 · %s" % (row["route"], row["len"], row["controls"], row["label"]))
    print("检查 %d 页，失败 %d 页" % (len(rows), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

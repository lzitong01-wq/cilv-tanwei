import { useEffect, useRef, useState } from "react";

type Bucket = {
  questions: number;
  raw: number;
  normalized: number;
  proxy: number;
  errors: number;
  avg_ms: number;
};

type Summary = {
  questions: number;
  /** 有标准答案的题数（上传的验证集可能不含答案） */
  scored?: number;
  unscored?: number;
  source?: string;
  raw_passed: number;
  normalized_passed: number;
  proxy_passed: number;
  errors: number;
  elapsed_ms: number;
  avg_ms: number;
  by_category: Record<string, Bucket>;
};

type Parsed = {
  format: string;
  total: number;
  with_gold: number;
  without_gold: number;
  categories: [string, number][];
  warnings: string[];
};

type JobStatus = {
  id: string;
  state: "running" | "done" | "failed";
  progress: number;
  summary: Summary | null;
  error: { code: string; message: string } | null;
  elapsed_ms: number;
};

type EvalItem = {
  qid: string;
  category: string;
  status: string;
  /** 没有标准答案时是 null（不是 false）——「没答案」和「答错」是两回事 */
  raw: boolean | null;
  normalized: boolean | null;
  proxy: boolean | null;
  answer: string;
  gold: string;
  fields?: Record<string, unknown>;
  error?: string | null;
  message?: string;
};

type Page = { items: EvalItem[]; total: number; page: number; page_size: number };

/** 判定显示：null = 这份验证集没有标准答案（不是「答错」）。 */
function mark(value: boolean | null | undefined) {
  if (value === null || value === undefined) {
    return <span className="muted" title="本次验证集没有标准答案，未评分">—</span>;
  }
  return value ? "✓" : "✕";
}

const CATEGORY_SHORT: Record<string, string> = {
  同调全篇声律比例差: "C1 比例差",
  跨篇分段变幅比较: "C2 分段变幅",
  跨篇长句仄声密度比较: "C3 长句密度",
  跨篇后段排序与比例差距: "C4 后段排序",
  跨篇景情节奏互证: "C5 景情节奏"
};

export default function EvalCenter() {
  const [job, setJob] = useState<JobStatus | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [pageNo, setPageNo] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [uploading, setUploading] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  async function run(limit?: number) {
    setPending(true);
    setError(null);
    setPage(null);
    setPageNo(1);
    try {
      const response = await fetch("/api/eval/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(limit ? { limit } : {})
      });
      const body = await response.json();
      if (!body.ok) throw new Error(body.error?.message ?? "启动失败");
      const jobId = body.data.job_id as string;
      poll(jobId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动失败");
      setPending(false);
    }
  }

  async function upload() {
    if (!file) {
      setError("先选一个验证集文件");
      return;
    }
    setUploading(true);
    setError(null);
    setPage(null);
    setPageNo(1);
    setParsed(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/eval/upload", { method: "POST", body: form });
      const body = await response.json();
      if (!body.ok) {
        const raw = body.error?.detail;
        const detail = typeof raw === "string"
          ? raw
          : raw && typeof raw === "object"
            ? Object.values(raw as Record<string, unknown>).map(String).join("；")
            : "";
        throw new Error((body.error?.message ?? "上传失败") + (detail ? "（" + detail + "）" : ""));
      }
      setParsed(body.data.parsed as Parsed);
      setUploading(false);
      poll(body.data.job_id as string);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
      setUploading(false);
    }
  }

  function downloadExample() {
    const sample = [
      { 题号: "V-001", 类别: "同调全篇声律比例差", 问题: "（把题面整段粘在这里）", 标准答案: "（可留空）" },
      { 题号: "V-002", 类别: "跨篇后段排序与比例差距", 问题: "（第二题）", 标准答案: "" }
    ];
    const blob = new Blob([JSON.stringify(sample, null, 2)], {
      type: "application/json;charset=utf-8"
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "验证集格式示例.json";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function exportAnswers(format: "jsonl" | "csv" | "xlsx") {
    if (!job) return;
    window.open(`/api/eval/${job.id}/export?format=${format}`, "_blank");
  }

  function poll(jobId: string) {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      try {
        const response = await fetch("/api/eval/" + jobId);
        const body = await response.json();
        if (!body.ok) throw new Error(body.error?.message ?? "查询失败");
        const status = body.data as JobStatus;
        setJob(status);
        if (status.state !== "running") {
          if (timer.current) window.clearInterval(timer.current);
          setPending(false);
          setUploading(false);
          loadItems(jobId, 1);
        }
      } catch (err) {
        if (timer.current) window.clearInterval(timer.current);
        setError(err instanceof Error ? err.message : "查询失败");
        setPending(false);
      }
    }, 400);
  }

  async function loadItems(jobId: string, target: number) {
    const response = await fetch(`/api/eval/${jobId}/items?page=${target}&page_size=10`);
    const body = await response.json();
    if (body.ok) {
      setPage(body.data as Page);
      setPageNo(target);
    }
  }

  const summary = job?.summary;

  return (
    <section>
      <h1>验证集测试</h1>
      <p className="sub">
        上传一份验证集，系统逐题调用内部求解接口并给出回答。题面可以不带标准答案——
        那样只产出回答、不算分，回答可导出后自行对答案。带答案时另给三口径评分：
        整段逐字、引文归一化、C5 代理字段。
      </p>

      <div className="panel">
        <div className="row" style={{ marginBottom: 8 }}>
          <b>上传验证集</b>
          <span className="muted">支持 .jsonl / .csv / .json / .xlsx，字段名自动识别；读不了会说明原因</span>
        </div>
        <div className="row">
          <input
            type="file"
            accept=".jsonl,.ndjson,.csv,.json,.xlsx,.xlsm,.xls,application/json,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            aria-label="选择验证集文件"
          />
          <button onClick={upload} disabled={uploading || !file}>
            {uploading ? "上传中…" : "上传并开始"}
          </button>
          <button onClick={downloadExample}>下载格式示例</button>
          <span className="muted" style={{ marginLeft: 8 }}>也支持模板4 的 .xlsx（两列：问题 / 预期答案）</span>
        </div>
        <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>
          每题至少要有「问题」字段；「题号」「类别」「标准答案」可选。列名兼容
          题号/qid、类别/category、问题/question、标准答案/answer。
        </div>
        {parsed && (
          <div className="note" style={{ marginTop: 10 }}>
            已解析 <b>{parsed.total}</b> 题（{parsed.format}）
            {parsed.with_gold > 0 ? `，其中 ${parsed.with_gold} 题带标准答案` : "，全部没有标准答案"}
            {parsed.categories.length > 0 &&
              ` · 类别：${parsed.categories.map(([name, count]) => `${name} ${count}`).join(" / ")}`}
            {parsed.warnings.map((item) => (
              <div key={item} className="muted">
                {item}
              </div>
            ))}
          </div>
        )}
      </div>

      <h2>用本机题库跑分</h2>

      <div className="row">
        <button onClick={() => run(20)} disabled={pending}>
          {pending ? "跑分中…" : "复跑 20 题"}
        </button>
        <button onClick={() => run()} disabled={pending}>
          全量 700 题
        </button>
        <span className="muted">同一次只允许一个任务；跑分不调用模型。</span>
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      {!job && !error && (
        <div className="empty">
          <div className="empty-mark" aria-hidden="true">評</div>
          <h3>按类别回放题库，看它到底对了多少</h3>
          <p className="sub">
            跑分不调用模型：引擎逐题重算，再和题库金标对照。700 题全量约 0.2 秒，
            结果按类别拆开，可以点进任意一题看字段、引文与计算链。
          </p>
          <div className="row">
            <span className="chip">整段逐字 · 普通题</span>
            <span className="chip">引文归一化 · C4</span>
            <span className="chip">变化值与方向 · C5</span>
          </div>
        </div>
      )}

      {job && (
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="row">
            <span className="pill">{job.state === "running" ? "运行中" : job.state === "done" ? "已完成" : "失败"}</span>
            <span className="muted">
              进度 {job.progress} 题 · 用时 {Math.round(job.elapsed_ms)} ms
            </span>
          </div>
          {job.error && <div className="note">{job.error.code}：{job.error.message}</div>}
        </div>
      )}

      {summary && (
        <>
          <div className="grid" style={{ marginTop: 12 }}>
            <div className="stat">
              <span>题数</span>
              <strong>{summary.questions}</strong>
            </div>
            {summary.scored !== undefined && summary.scored > 0 && (
              <>
            <div className="stat">
              <span>整段逐字</span>
              <strong>
                {summary.raw_passed}
                <em style={{ fontStyle: "normal", fontSize: 12, color: "var(--ink-3)" }}>
                  {" "}/ {summary.questions}
                </em>
              </strong>
            </div>
            <div className="stat">
              <span>引文归一化</span>
              <strong>
                {summary.normalized_passed}
                <em style={{ fontStyle: "normal", fontSize: 12, color: "var(--ink-3)" }}>
                  {" "}/ {summary.questions}
                </em>
              </strong>
            </div>
            <div className="stat">
              <span>代理口径（含 C5）</span>
              <strong>
                {summary.proxy_passed}
                <em style={{ fontStyle: "normal", fontSize: 12, color: "var(--ink-3)" }}>
                  {" "}/ {summary.questions}
                </em>
              </strong>
            </div>
              </>
            )}
            <div className="stat">
              <span>错误</span>
              <strong>{summary.errors}</strong>
            </div>
            <div className="stat">
              <span>平均耗时</span>
              <strong>{summary.avg_ms} ms</strong>
            </div>
          </div>

          {summary.scored !== undefined && summary.scored === 0 && (
            <div className="note" style={{ marginTop: 8 }}>
              这份验证集没有标准答案，所以不计分——下面是逐题回答，导出后自行对答案即可。
            </div>
          )}

          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={() => exportAnswers("jsonl")}>导出回答（JSONL）</button>
            <button onClick={() => exportAnswers("csv")}>导出回答（CSV）</button>
            <button onClick={() => exportAnswers("xlsx")}>导出结果 Excel</button>
            <span className="muted">Excel 按模板5 五列：问题 / 预期答案 / 智能体实际回复 / 耗时(秒) / 状态</span>
          </div>

          <h2>分类别</h2>
          <table>
            <thead>
              <tr>
                <th>类别</th>
                <th style={{ width: 64 }}>题数</th>
                <th style={{ width: 80 }}>整段</th>
                <th style={{ width: 80 }}>归一化</th>
                <th style={{ width: 80 }}>代理</th>
                <th style={{ width: 72 }}>均耗时</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(summary.by_category).map(([category, bucket]) => (
                <tr key={category}>
                  <td>{CATEGORY_SHORT[category] ?? category}</td>
                  <td className="num">{bucket.questions}</td>
                  <td className="num">{summary?.scored ? bucket.raw : "—"}</td>
                  <td className="num">{summary?.scored ? bucket.normalized : "—"}</td>
                  <td className="num">{summary?.scored ? bucket.proxy : "—"}</td>
                  <td className="num">{bucket.avg_ms} ms</td>
                </tr>
              ))}
            </tbody>
          </table>

          {page && (
            <>
              <h2>逐题（{page.total} 题）</h2>
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 110 }}>题号</th>
                    <th style={{ width: 100 }}>类别</th>
                    <th style={{ width: 64 }}>整段</th>
                    <th style={{ width: 72 }}>归一化</th>
                    <th style={{ width: 56 }}>代理</th>
                    <th>我们的答案</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((item) => (
                    <tr key={item.qid}>
                      <td className="mono">{item.qid}</td>
                      <td>{CATEGORY_SHORT[item.category] ?? item.category}</td>
                      <td>{mark(item.raw)}</td>
                      <td>{mark(item.normalized)}</td>
                      <td>{mark(item.proxy)}</td>
                      <td style={{ fontSize: 11 }}>
                        {item.answer ? item.answer.slice(0, 80) : item.error ?? item.message ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row" style={{ marginTop: 8 }}>
                <button
                  onClick={() => job && loadItems(job.id, pageNo - 1)}
                  disabled={pageNo <= 1}
                >
                  上一页
                </button>
                <span className="muted">
                  第 {pageNo} / {Math.max(1, Math.ceil(page.total / 10))} 页
                </span>
                <button
                  onClick={() => job && loadItems(job.id, pageNo + 1)}
                  disabled={pageNo >= Math.ceil(page.total / 10)}
                >
                  下一页
                </button>
              </div>
            </>
          )}

          <div className="note">
            整段差异集中在 C4/C5，原因是金标任选引文；C5 的引文没有引号包裹，归一化对它不适用，故以代理口径为准。
            字段级断言的独立复核见 tools/benchmark_engine.py。
          </div>
        </>
      )}
    </section>
  );
}

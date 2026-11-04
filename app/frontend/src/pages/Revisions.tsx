import { useCallback, useEffect, useState } from "react";

type Stats = {
  total: number;
  by_status: { pending: number; accepted: number; rejected: number };
  by_kind: Record<string, number>;
  exported: number;
  closure_level: string;
};

type Item = {
  id: string;
  work_id: number;
  title: string;
  poet: string;
  period: string;
  line_no: number;
  line_text: string;
  local: { ping: number; ze: number };
  api: { ping: number; ze: number };
  kind: string;
  reason: string;
  candidates: string[];
  status: string;
  note: string | null;
  exported_at: string | null;
  suggestion: {
    char: string;
    reading: string;
    exact_context: boolean;
    basis: string;
    context: string;
  } | null;
};

const KIND_LABEL: Record<string, string> = {
  polyphone: "多音字可解释",
  unresolved: "暂无法解释"
};

const STATUS_LABEL: Record<string, string> = {
  pending: "待复核",
  accepted: "已接受",
  rejected: "已驳回"
};

export default function Revisions() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("pending");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const kindPart = kind ? `&kind=${kind}` : "";
      const [statsRes, listRes] = await Promise.all([
        fetch("/api/revisions/stats"),
        fetch(`/api/revisions?status=${status}${kindPart}&page=${page}&page_size=10`)
      ]);
      const statsBody = await statsRes.json();
      const listBody = await listRes.json();
      if (!statsBody.ok) throw new Error(statsBody.error?.message ?? "统计失败");
      if (!listBody.ok) throw new Error(listBody.error?.message ?? "列表失败");
      setStats(statsBody.data as Stats);
      setItems(listBody.data.items as Item[]);
      setTotal(listBody.data.total as number);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [status, kind, page]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(id: string, decision: "accept" | "reject") {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`/api/revisions/${id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note: decision === "accept" ? "按官方计数采纳" : "本地读音更可信" })
      });
      const body = await response.json();
      if (!body.ok) throw new Error(body.error?.message ?? "操作失败");
      await load();
      setMessage(decision === "accept" ? "已接受该条，可在导出时一并带出" : "已驳回，保留本地判断");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function exportCsv() {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const response = await fetch("/api/revision/export", { method: "POST" });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.error?.message ?? "导出失败");
      }
      const key = response.headers.get("X-Export-Key") ?? "unknown";
      const rows = response.headers.get("X-Export-Rows") ?? "0";
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `revisions_${key}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage(`已导出 ${rows} 条，导出键 ${key}（同样内容重复导出会得到同一个键）`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>差异校订</h1>
      <p className="sub">
        差异只表示「本地计算与官方标注不一致」，不断言谁错。闭环止于导出，写回由官方流程完成。
      </p>

      {stats && (
        <>
          <div className="grid" style={{ marginBottom: 12 }}>
            <div className="stat">
              <span>待复核</span>
              <strong>{stats.by_status.pending}</strong>
            </div>
            <div className="stat">
              <span>已接受</span>
              <strong>{stats.by_status.accepted}</strong>
            </div>
            <div className="stat">
              <span>已驳回</span>
              <strong>{stats.by_status.rejected}</strong>
            </div>
            <div className="stat">
              <span>已导出</span>
              <strong>{stats.exported}</strong>
            </div>
            <div className="stat">
              <span>可解释 / 总数</span>
              <strong>{stats.by_kind.polyphone ?? 0} / {stats.total}</strong>
            </div>
          </div>
          <div className="row" style={{ marginBottom: 10 }}>
            <span className="pill">{stats.closure_level}</span>
            <span className="muted">暂无法解释 {stats.by_kind.unresolved ?? 0} 条，单列不硬给建议</span>
          </div>
        </>
      )}

      <div className="row">
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setPage(1);
          }}
          aria-label="状态筛选"
          className="field"
        >
          <option value="pending">待复核</option>
          <option value="accepted">已接受</option>
          <option value="rejected">已驳回</option>
        </select>
        <select
          value={kind}
          onChange={(event) => {
            setKind(event.target.value);
            setPage(1);
          }}
          aria-label="类型筛选"
          className="field"
        >
          <option value="">全部类型</option>
          <option value="polyphone">多音字可解释（有建议读音）</option>
          <option value="unresolved">暂无法解释</option>
        </select>
        <button onClick={exportCsv} disabled={busy}>
          {busy ? "处理中…" : "导出已接受条目"}
        </button>
        <span className="muted">共 {total} 条</span>
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
      {message && <div className="note" style={{ marginTop: 12 }}>{message}</div>}

      <table style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th style={{ width: 150 }}>作品 / 句</th>
            <th>原句</th>
            <th style={{ width: 104 }}>本地 → 官方</th>
            <th style={{ width: 96 }}>类型</th>
            <th style={{ width: 150 }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                <div>{item.title}</div>
                <div className="mono muted">
                  {item.poet} · 第 {item.line_no} 句
                </div>
              </td>
              <td>
                <div>{item.line_text}</div>
                <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{item.reason}</div>
                {item.candidates.length > 0 && (
                  <div className="mono muted" style={{ fontSize: 11 }}>
                    候选多音字：{item.candidates.join("、")}
                  </div>
                )}
                {item.suggestion && (
                  <div style={{ fontSize: 11, marginTop: 3 }}>
                    <span className="pill verified">
                      建议：{item.suggestion.char} 读 {item.suggestion.reading}
                    </span>
                    <span className="muted" style={{ marginLeft: 6 }}>
                      {item.suggestion.exact_context ? "按上下文规则命中" : "按字级兜底推断，泛化弱，需人工确认"}
                    </span>
                  </div>
                )}
              </td>
              <td className="num">
                {item.local.ping}/{item.local.ze}
                <span className="muted"> → </span>
                {item.api.ping}/{item.api.ze}
              </td>
              <td>
                <span className="pill" style={{ borderColor: item.kind === "polyphone" ? "var(--ok)" : "var(--warn)", color: item.kind === "polyphone" ? "var(--ok)" : "var(--warn)" }}>
                  {KIND_LABEL[item.kind] ?? item.kind}
                </span>
              </td>
              <td>
                {item.status === "pending" ? (
                  <div className="row" style={{ gap: 6 }}>
                    <button onClick={() => decide(item.id, "accept")} disabled={busy}>
                      接受
                    </button>
                    <button onClick={() => decide(item.id, "reject")} disabled={busy}>
                      驳回
                    </button>
                  </div>
                ) : (
                  <span className="muted">
                    {STATUS_LABEL[item.status] ?? item.status}
                    {item.exported_at ? " · 已导出" : ""}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page <= 1 || busy}>
          上一页
        </button>
        <span className="muted">第 {page} / {Math.max(1, Math.ceil(total / 10))} 页</span>
        <button
          onClick={() => setPage((value) => value + 1)}
          disabled={page >= Math.ceil(total / 10) || busy}
        >
          下一页
        </button>
      </div>
    </section>
  );
}

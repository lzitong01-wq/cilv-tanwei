import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ExportHistory } from "../api";

export default function Exports() {
  const [history, setHistory] = useState<ExportHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setHistory(await api.exportHistory(100));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出记录读取失败");
    }
  }

  useEffect(() => {
    void load();
  }, []);

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
      <h1>导出记录</h1>
      <p className="sub">
        每次导出留一条记录：时间、幂等键（本次导出条目 ID 的内容哈希）、行数。
        同样内容重复导出得到同一个键，不会产生重复回写。闭环止于导出，写回由官方流程完成。
      </p>

      <div className="row">
        <button onClick={exportCsv} disabled={busy}>
          {busy ? "导出中…" : "导出已接受的校订条目"}
        </button>
        <Link to="/revisions">
          <button>去差异校订</button>
        </Link>
        <button onClick={load}>刷新</button>
      </div>

      {message && <div className="note" style={{ marginTop: 10 }}>{message}</div>}
      {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}

      {history && history.items.length === 0 ? (
        <div className="panel" style={{ marginTop: 12 }}>
          还没有导出记录。先在差异校订里接受若干条目，再回到本页导出。
        </div>
      ) : (
        history && (
          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th style={{ width: 160 }}>时间</th>
                <th style={{ width: 200 }}>幂等键</th>
                <th style={{ width: 90 }}>本次行数</th>
                <th style={{ width: 100 }}>已接受</th>
                <th style={{ width: 100 }}>待复核</th>
                <th>文件名</th>
              </tr>
            </thead>
            <tbody>
              {history.items.map((item) => (
                <tr key={item.id}>
                  <td className="mono">{item.created_at}</td>
                  <td className="mono">{item.export_key}</td>
                  <td className="num">{item.row_count}</td>
                  <td className="num">{item.accepted_total}</td>
                  <td className="num">{item.pending_total}</td>
                  <td className="mono muted">{item.filename}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}

      {history && (
        <div className="muted" style={{ marginTop: 10 }}>
          闭环级别：{history.closure_level}
        </div>
      )}
    </section>
  );
}

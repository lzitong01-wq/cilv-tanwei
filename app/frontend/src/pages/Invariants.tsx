import { useEffect, useState } from "react";

type Invariant = {
  id: string;
  desc: string;
  case: string;
  status: "claimed" | "pending";
  note?: string;
};

type Registry = { contract: string; note: string; invariants: Invariant[] };

export default function Invariants() {
  const [data, setData] = useState<Registry | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/invariants")
      .then((response) => response.json())
      .then((body) => {
        if (!body.ok) throw new Error(body.error?.message ?? "读取失败");
        setData(body.data as Registry);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const claimed = data?.invariants.filter((item) => item.status === "claimed").length ?? 0;
  const pending = (data?.invariants.length ?? 0) - claimed;

  return (
    <section>
      <h1>不变量与用例</h1>
      <p className="sub">
        每条红线都绑定一个真实用例。`scripts/check_invariants.py` 用 ast 检查用例名确实存在，
        不是只看文件在不在；pending 项如实挂起、不计入通过率。
      </p>

      {error && <div className="error">{error}</div>}

      {data && (
        <>
          <div className="grid">
            <div className="stat"><span>契约版本</span><strong className="mono">{data.contract}</strong></div>
            <div className="stat"><span>已认领</span><strong>{claimed}</strong></div>
            <div className="stat"><span>挂起</span><strong>{pending}</strong></div>
            <div className="stat"><span>合计</span><strong>{data.invariants.length}</strong></div>
          </div>

          <h2>登记表</h2>
          <table style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th style={{ width: 150 }}>编号</th>
                <th>约束</th>
                <th style={{ width: 90 }}>状态</th>
                <th style={{ width: 320 }}>认领用例</th>
              </tr>
            </thead>
            <tbody>
              {data.invariants.map((item) => (
                <tr key={item.id}>
                  <td className="mono">{item.id}</td>
                  <td>
                    {item.desc}
                    {item.note && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{item.note}</div>}
                  </td>
                  <td>
                    <span className={`pill ${item.status === "claimed" ? "verified" : "unverified"}`}>
                      {item.status === "claimed" ? "已认领" : "挂起"}
                    </span>
                  </td>
                  <td className="mono" style={{ fontSize: 10 }}>{item.case || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="note" style={{ marginTop: 10 }}>{data.note}</div>
        </>
      )}
    </section>
  );
}

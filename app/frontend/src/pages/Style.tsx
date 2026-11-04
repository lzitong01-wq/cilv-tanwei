import { useEffect, useMemo, useState } from "react";

type Author = { poet: string; works: number };

type StyleOne = {
  poet: string;
  works: number;
  tune_diversity: number;
  mean_line_length: number;
  line_length_sd: number;
  ze_ratio: number;
  long_line_rate: number;
  top_tunes: [string, number][];
  top_images: [string, number][];
  tier_label: string;
};

type Difference = { field: string; label: string; a: number | null; b: number | null; delta: number | null };

type Compare = { a: StyleOne; b: StyleOne; differences: Difference[] };

type Guarded = {
  status: "ok" | "blocked" | "degraded";
  candidate: { draft: string; basis: string[]; verification: string | null; boundary: string | null } | null;
  checks: Record<string, boolean> | null;
  violations: Record<string, unknown> | null;
  model: string;
  latency_ms: number;
  fallback_reason: string | null;
};

const FIELD_UNIT: Record<string, string> = {
  works: "首",
  tune_diversity: "种",
  mean_line_length: "字",
  line_length_sd: "字",
  ze_ratio: "%",
  long_line_rate: ""
};

// 比率类字段有天然上限，用固定量程才看得出一方是 41% 还是 90%；
// 计数类字段没有上限，只能按两者最大值归一到满格。
const NATURAL_MAX: Record<string, number> = { ze_ratio: 100, long_line_rate: 1 };

function bar(value: number | null, max: number) {
  if (value === null || max <= 0) return "0%";
  return Math.max(2, Math.min(100, (value / max) * 100)) + "%";
}

export default function Style() {
  const [authors, setAuthors] = useState<Author[]>([]);
  const [left, setLeft] = useState("汪东");
  const [right, setRight] = useState("朱祖谋");
  const [pair, setPair] = useState<Compare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dual, setDual] = useState<Record<string, Guarded> | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    fetch("/api/style/authors?limit=200")
      .then((res) => res.json())
      .then((body) => {
        if (body.ok) setAuthors(body.data.items as Author[]);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!left || !right || left === right) {
      setPair(null);
      return;
    }
    fetch(`/api/style/compare?a=${encodeURIComponent(left)}&b=${encodeURIComponent(right)}`)
      .then((res) => res.json())
      .then((body) => {
        if (body.ok) {
          setPair(body.data as Compare);
          setError(null);
        } else {
          setError(body.error?.message ?? "对照失败");
        }
      })
      .catch((err: Error) => setError(err.message));
  }, [left, right]);

  async function explain() {
    setPending(true);
    setError(null);
    setDual(null);
    try {
      const response = await fetch("/api/style/narrative", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ a: left, b: right, dual: true })
      });
      const body = await response.json();
      if (!body.ok) throw new Error(body.error?.message ?? "生成失败");
      setDual(body.data.models as Record<string, Guarded>);
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败");
    } finally {
      setPending(false);
    }
  }

  const maxima = useMemo(() => {
    if (!pair) return {} as Record<string, number>;
    const map: Record<string, number> = {};
    for (const item of pair.differences) {
      map[item.field] = Math.max(Number(item.a ?? 0), Number(item.b ?? 0), 1);
    }
    return map;
  }, [pair]);

  return (
    <section>
      <h1>风格画像与对比</h1>
      <p className="sub">
        特征全部由构建期统计得出，可复算；解读由模型基于同一张特征卡生成，两个模型各写一段，都必须过闸门。
      </p>

      <div className="row">
        <select value={left} onChange={(event) => setLeft(event.target.value)} aria-label="词人甲"
          style={{ padding: "6px 10px", border: "1px solid var(--rule)", borderRadius: 2, background: "var(--surface)", minWidth: 130 }}>
          {authors.map((item) => (
            <option key={item.poet} value={item.poet}>
              {item.poet}（{item.works} 首）
            </option>
          ))}
        </select>
        <span className="muted">对比</span>
        <select value={right} onChange={(event) => setRight(event.target.value)} aria-label="词人乙"
          style={{ padding: "6px 10px", border: "1px solid var(--rule)", borderRadius: 2, background: "var(--surface)", minWidth: 130 }}>
          {authors.map((item) => (
            <option key={item.poet} value={item.poet}>
              {item.poet}（{item.works} 首）
            </option>
          ))}
        </select>
        <button onClick={explain} disabled={pending || !pair}>
          {pending ? "生成中…" : "双模型解读"}
        </button>
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      {pair && (
        <>
          <h2>形式特征对照</h2>
          {pair.differences.map((item) => (
            <div key={item.field} style={{ marginBottom: 10 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">{item.label}</span>
                <span className="mono">
                  {item.a} {FIELD_UNIT[item.field]} → {item.b} {FIELD_UNIT[item.field]}
                  {item.delta !== null && (
                    <span style={{ color: item.delta > 0 ? "var(--cinnabar)" : "var(--indigo)", marginLeft: 8 }}>
                      {item.delta > 0 ? "+" : ""}
                      {item.delta}
                    </span>
                  )}
                </span>
              </div>
              <div style={{ display: "grid", gap: 2, marginTop: 3 }}>
                <div style={{ height: 6, background: "var(--track)", position: "relative" }}>
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: bar(item.a, NATURAL_MAX[item.field] ?? maxima[item.field]),
                      background: "var(--indigo)"
                    }}
                  />
                </div>
                <div style={{ height: 6, background: "var(--track)", position: "relative" }}>
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: bar(item.b, NATURAL_MAX[item.field] ?? maxima[item.field]),
                      background: "var(--cinnabar)"
                    }}
                  />
                </div>
              </div>
            </div>
          ))}
          <div className="row" style={{ marginTop: 4 }}>
            <span className="pill" style={{ borderColor: "var(--indigo)", color: "var(--indigo)" }}>{pair.a.poet}</span>
            <span className="pill" style={{ borderColor: "var(--cinnabar)", color: "var(--cinnabar)" }}>{pair.b.poet}</span>
            <span className="muted">口径：{pair.a.tier_label}；{pair.a.tier_label === pair.b.tier_label ? "两侧一致" : "两侧口径不同"}</span>
          </div>

          <h2>常用词牌与题材词频</h2>
          <table>
            <thead>
              <tr>
                <th style={{ width: 90 }}>词人</th>
                <th style={{ width: "44%" }}>常用词牌</th>
                <th>题材词频</th>
              </tr>
            </thead>
            <tbody>
              {[pair.a, pair.b].map((style) => (
                <tr key={style.poet}>
                  <td>{style.poet}</td>
                  <td>{style.top_tunes.map(([name, count]) => `${name}×${count}`).join("　")}</td>
                  <td>{style.top_images.map(([name, count]) => `${name}×${count}`).join("　")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {dual && (
        <>
          <h2>两模型解读对照</h2>
          <div className="split-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
            {Object.entries(dual).map(([label, item]) => (
              <div className="panel" key={label}>
                <div className="row">
                  <span className="pill">{item.model}</span>
                  <span className="pill">{item.latency_ms} ms</span>
                  <span className="pill unverified">
                    {item.status === "ok" ? "候选·未验证" : item.status === "blocked" ? "未过闸门" : "已降级"}
                  </span>
                </div>
                {item.status === "ok" && item.candidate ? (
                  <>
                    <div className="answer" style={{ marginTop: 8, fontSize: 13 }}>
                      {item.candidate.draft}
                      <div className="sub" style={{ marginTop: 8, marginBottom: 0 }}>
                        {String(item.candidate.boundary ?? "")}
                      </div>
                    </div>
                    <div className="mono muted" style={{ marginTop: 6 }}>
                      依据：{item.candidate.basis.join("、")}
                    </div>
                  </>
                ) : item.status === "blocked" ? (
                  <div className="error" style={{ marginTop: 8 }}>
                    未过闸门：<span className="mono">{JSON.stringify(item.violations)}</span>
                  </div>
                ) : (
                  <div className="note" style={{ marginTop: 8 }}>
                    模型未给出候选：{item.fallback_reason ?? "不可用"}
                  </div>
                )}
                {item.checks && (
                  <div className="row" style={{ marginTop: 8 }}>
                    {Object.entries(item.checks).map(([key, passed]) => (
                      <span key={key} className="pill"
                        style={{ borderColor: passed ? "var(--ok)" : "var(--cinnabar)", color: passed ? "var(--ok)" : "var(--cinnabar)" }}>
                        {key} {passed ? "✓" : "×"}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="note">
            两段解读都只是模型对同一张特征卡的措辞，差异反映模型的表达习惯，不构成对词人的评价。
          </div>
        </>
      )}
    </section>
  );
}

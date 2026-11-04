import { useEffect, useMemo, useState } from "react";
import { api, type SentimentBenchmark, type SentimentCandidateResult } from "../api";

export default function Sentiment() {
  const [bench, setBench] = useState<SentimentBenchmark | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [text, setText] = useState("灯花又作落花红，鬓影风情休问。");
  const [result, setResult] = useState<SentimentCandidateResult | null>(null);
  const [pending, setPending] = useState(false);
  const [candidateError, setCandidateError] = useState<string | null>(null);

  useEffect(() => {
    api
      .sentimentBenchmark()
      .then(setBench)
      .catch((err: Error) => setError(err.message));
  }, []);

  const rows = useMemo(() => {
    const items = bench?.items ?? [];
    const keyword = filter.trim();
    if (!keyword) return items.slice(0, 60);
    return items.filter(
      (item) =>
        item.label.includes(keyword) ||
        item.text.includes(keyword) ||
        item.poet.includes(keyword)
    );
  }, [bench, filter]);

  async function ask() {
    setPending(true);
    setCandidateError(null);
    setResult(null);
    try {
      setResult(await api.sentimentCandidate({ text }));
    } catch (err) {
      setCandidateError(err instanceof Error ? err.message : "生成失败");
    } finally {
      setPending(false);
    }
  }

  const metrics = bench?.metrics;

  return (
    <section>
      <h1>情感候选</h1>
      <p className="sub">
        声律事实由引擎给出；情感只提供**方向候选**，一律标「未验证」。
        评估用的参考集合由模型按成文规范生成，不是人工金标。
      </p>

      <div className="note" style={{ borderLeftWidth: 3 }}>
        <b>披露：</b>
        {bench?.disclosure ?? "基准集由模型标注、未经人工核验；不是人工金标，也不用于训练。"}
        相关数字一律表述为「与 AI 标注基准集的一致率」，不表述为「情感识别准确率」。
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      {metrics && (
        <>
          <h2>基准集指标</h2>
          <div className="grid">
            <div className="stat">
              <span>基准集句数</span>
              <strong>{metrics.benchmark_size}</strong>
            </div>
            <div className="stat">
              <span>与基准集一致率</span>
              <strong style={{ fontSize: 18 }}>{(metrics.consistency_with_benchmark * 100).toFixed(1)}%</strong>
            </div>
            <div className="stat">
              <span>标注者间 κ</span>
              <strong style={{ fontSize: 18 }}>{metrics.kappa_vs_majority.toFixed(3)}</strong>
            </div>
            <div className="stat">
              <span>三轮完全一致率</span>
              <strong style={{ fontSize: 18 }}>{(metrics.agreement_rate * 100).toFixed(1)}%</strong>
            </div>
            <div className="stat">
              <span>分歧率</span>
              <strong style={{ fontSize: 18 }}>{(metrics.disputed_ratio * 100).toFixed(1)}%</strong>
            </div>
            <div className="stat">
              <span>盲测句数</span>
              <strong>{metrics.blind_size}</strong>
            </div>
          </div>

          <div className="row" style={{ marginTop: 10 }}>
            <span className="muted">规范 {bench?.spec_version}</span>
            <span className="muted">种子 {bench?.seed}</span>
            <span className="muted mono">hash {bench?.sample_hash.slice(0, 12)}</span>
            <span className="muted">构建于 {bench?.built_at.slice(0, 16).replace("T", " ")}</span>
          </div>

          <h2>标注轮次</h2>
          <table>
            <thead>
              <tr>
                <th style={{ width: 70 }}>轮次</th>
                <th style={{ width: 170 }}>模型</th>
                <th style={{ width: 90 }}>措辞变体</th>
                <th style={{ width: 90 }}>选项顺序</th>
                <th>两两 κ（与其它轮次）</th>
              </tr>
            </thead>
            <tbody>
              {bench?.rounds.map((round) => (
                <tr key={round.key}>
                  <td className="mono">{round.key}</td>
                  <td className="mono">{round.model}</td>
                  <td>{round.variant}</td>
                  <td className="mono">{round.order}</td>
                  <td className="mono">
                    {Object.entries(metrics.kappa_pairs)
                      .filter(([pair]) => pair.split("-").includes(round.key))
                      .map(([pair, value]) => `${pair} ${value.toFixed(3)}`)
                      .join("　") || "—"}
                  </td>
                </tr>
              ))}
              <tr>
                <td className="mono">{bench?.arbiter.key}</td>
                <td className="mono">{bench?.arbiter.model}</td>
                <td colSpan={2}>分歧仲裁（更严格规范）</td>
                <td className="num">—</td>
              </tr>
            </tbody>
          </table>

          <h2>标签分布</h2>
          <div className="panel">
            {Object.entries(metrics.label_distribution)
              .sort((a, b) => b[1] - a[1])
              .map(([label, count]) => {
                const max = Math.max(...Object.values(metrics.label_distribution));
                return (
                  <div key={label} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 88, fontSize: 12 }}>{label}</span>
                    <div style={{ height: 8, width: "100%", background: "var(--track)", position: "relative" }}>
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          width: `${(count / max) * 100}%`,
                          background: "var(--indigo)"
                        }}
                      />
                    </div>
                    <span className="mono muted" style={{ width: 42, textAlign: "right" }}>{count}</span>
                  </div>
                );
              })}
          </div>
        </>
      )}

      <h2>试一句（产品路径）</h2>
      <div className="row">
        <input
          className="field"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="输入一句词"
          aria-label="待分析句子"
          style={{ flex: 1, minWidth: 260 }}
        />
        <button onClick={ask} disabled={pending}>
          {pending ? "生成中…" : "生成方向候选"}
        </button>
      </div>
      {candidateError && <div className="error" style={{ marginTop: 10 }}>{candidateError}</div>}
      {result && (
        <div className="panel" style={{ marginTop: 10 }}>
          <div className="row">
            <span className={`pill ${result.status === "ok" ? "verified" : "unverified"}`}>
              {result.status === "ok" ? "候选已展示" : result.status === "blocked" ? "未通过闸门" : "已降级"}
            </span>
            {result.direction && <span className="pill">{result.direction}</span>}
            {result.strength !== null && <span className="pill">强度 {result.strength}</span>}
            <span className="muted mono">{result.model} · {result.latency_ms} ms</span>
          </div>
          {result.candidate && (
            <>
              <div className="answer" style={{ marginTop: 10, fontSize: 14 }}>
                {result.candidate.draft}
              </div>
              <div className="kv" style={{ marginTop: 8 }}>
                <span>验证状态</span>
                <b>{result.candidate.verification}</b>
              </div>
              <div className="kv">
                <span>依据</span>
                <b className="mono">{(result.candidate.basis ?? []).join(" · ")}</b>
              </div>
              <div className="kv">
                <span>边界</span>
                <b style={{ textAlign: "left" }}>{result.candidate.boundary}</b>
              </div>
            </>
          )}
          {!result.candidate && result.fallback_reason && (
            <div className="note" style={{ marginTop: 10 }}>{result.fallback_reason}</div>
          )}
          {result.checks && (
            <div className="row" style={{ marginTop: 10 }}>
              {Object.entries(result.checks).map(([name, ok]) => (
                <span key={name} className={`pill ${ok ? "verified" : "unverified"}`}>
                  {name} {ok ? "通过" : "未通过"}
                </span>
              ))}
            </div>
          )}
          <div className="note" style={{ marginTop: 10 }}>
            事实卡口径：{result.fact_card.tier_label}；句内意象线索：
            {JSON.stringify(result.fact_card.facts.image_hints)}。
            方向只是形式线索层面的指向，不是情感判定。
          </div>
        </div>
      )}

      <h2>基准集抽样明细</h2>
      <div className="row">
        <input
          className="field"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="按标签／句子／词人过滤"
          aria-label="过滤抽样明细"
          style={{ flex: 1, maxWidth: 320 }}
        />
        <span className="muted">显示 {rows.length} 条（最多 60）</span>
      </div>
      <table style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th style={{ width: 220 }}>句子</th>
            <th style={{ width: 110 }}>作者 · 词牌</th>
            <th style={{ width: 120 }}>层</th>
            <th style={{ width: 120 }}>三轮票</th>
            <th style={{ width: 110 }}>基准标签</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id}>
              <td>
                <div style={{ fontFamily: "var(--font-song)" }}>{item.text}</div>
                <div className="muted mono" style={{ fontSize: 10 }}>
                  #{item.work_id} 第 {item.line_no} 句
                  {item.inference_violation ? " · 含推断表述已作废" : ""}
                </div>
              </td>
              <td>
                <div>{item.poet}</div>
                <div className="muted" style={{ fontSize: 11 }}>{item.tune}</div>
              </td>
              <td className="mono" style={{ fontSize: 10 }}>{item.layer}</td>
              <td className="mono" style={{ fontSize: 10 }}>{item.votes.join(" / ")}</td>
              <td>
                {item.label}
                <div className="muted" style={{ fontSize: 10 }}>
                  {item.label_source}
                  {item.strength !== null ? ` · 强度均值 ${item.strength}` : ""}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

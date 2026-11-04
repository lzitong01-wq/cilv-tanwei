import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiFailure, api, type AgentResult, type SolveResult, type SolveTrace } from "../api";

type LocationState = { question?: string; category?: string } | null;

export default function Solve() {
  const location = useLocation();
  const navigate = useNavigate();
  const preset = (location.state as LocationState)?.question ?? "";
  const [question, setQuestion] = useState(preset);
  const [result, setResult] = useState<SolveResult | null>(null);
  const [agent, setAgent] = useState<AgentResult | null>(null);
  const [agentMode, setAgentMode] = useState(true);
  const [error, setError] = useState<{ code: string; message: string; trace: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [trace, setTrace] = useState<SolveTrace | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);

  async function reloadTrace(solveId: string) {
    setTraceError(null);
    try {
      setTrace(await api.solveTrace(solveId));
    } catch (err) {
      setTrace(null);
      setTraceError(err instanceof Error ? err.message : "推理链获取失败");
    }
  }

  async function submit() {
    if (!question.trim()) {
      setError({ code: "INVALID_INPUT", message: "题面不能为空", trace: "" });
      return;
    }
    setPending(true);
    setError(null);
    setResult(null);
    setAgent(null);
    setTrace(null);
    setTraceError(null);
    try {
      if (agentMode) {
        // 智能体模式：引擎优先，答不上来才交给模型
        const answered = await api.agentAnswer(question, true);
        setAgent(answered);
      } else {
        const solved = await api.solve(question);
        setResult(solved);
        if (solved.solve_id) void reloadTrace(solved.solve_id);
      }
    } catch (err) {
      if (err instanceof ApiFailure) {
        setError({ code: err.code, message: err.message, trace: err.traceId });
      } else {
        setError({ code: "NETWORK", message: err instanceof Error ? err.message : "请求失败", trace: "" });
      }
    } finally {
      setPending(false);
    }
  }

  // 智能体模式优先：它的字段/证据/计算链与确定性路径同构，只是多一段解读
  const view = (agent ?? result) as (AgentResult | SolveResult) | null;

  return (
    <section>
      <h1>题面求解</h1>
      <p className="sub">
        粘贴题库题面。默认走智能体模式：**引擎先按官方规则复算，答不上来的题型才交给模型**；
        关掉则只跑确定性路径（更快，判分走这条）。
      </p>

      <div className="row" style={{ marginBottom: 10 }}>
        <button
          className={agentMode ? "chip active-chip" : "chip"}
          onClick={() => setAgentMode(true)}
          title="引擎优先；答不上来时由模型依据事实卡兜底作答（标未验证）"
        >
          智能体模式
        </button>
        <button
          className={!agentMode ? "chip active-chip" : "chip"}
          onClick={() => setAgentMode(false)}
          title="只跑确定性引擎，不调用模型；评测判分走这条"
        >
          严格确定性
        </button>
        <span className="muted">
          {agentMode
            ? "数字与计算链仍由引擎给出；模型只写解读，答不上来时兜底。"
            : "只跑引擎，不调用模型。"}
        </span>
      </div>

      <textarea
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        placeholder="粘贴完整题面：包含甲/乙（丙）作品锚点与题面规则…"
        aria-label="题面"
      />
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={submit} disabled={pending}>
          {pending ? "计算中…" : agentMode ? "求解（智能体）" : "求解（确定性）"}
        </button>
        <button onClick={() => setQuestion("")} disabled={pending}>
          清空
        </button>
        <span className="muted">
          可确定复算的题型给可核对字段；超出规则覆盖时{agentMode ? "由模型依据事实卡作答并标未验证" : "明确返回边界，不给近似值"}。
        </span>
      </div>

      {error && (
        <div className="error" style={{ marginTop: 14 }}>
          <div>
            <span className="pill unverified">{error.code}</span> {error.message}
          </div>
          {error.trace && <div className="mono muted" style={{ marginTop: 6 }}>trace {error.trace}</div>}
        </div>
      )}

      {view && (
        <>
          <h2>答案</h2>
          <div className="row" style={{ marginBottom: 8 }}>
            {view.mode && (
              <span
                className="pill"
                title={
                  view.mode === "模型兜底（未验证）"
                    ? "这类题超出确定性规则覆盖，由模型依据事实卡作答，未经验证"
                    : "数字与计算链由确定性引擎给出，可复算"
                }
              >
                作答方式：{view.mode}
              </span>
            )}
            {view.source && (
              <span
                className="pill"
                title={
                  view.source === "官方 API 实时查询"
                    ? "这篇作品不在本地快照里，实时向官方知识库查询后计算"
                    : "数据来自本地冻结的官方快照"
                }
              >
                数据来源：{view.source}
              </span>
            )}
            {view.fallback && <span className="pill unverified">未验证 · 不作判分依据</span>}
          </div>
          <div className="answer">{view.answer}</div>

          {view.fallback && (
            <div className="note" style={{ marginTop: 8 }}>
              兜底原因：{view.fallback.reason}。{view.fallback.note}
            </div>
          )}

          {view.narrative && view.narrative.candidate && (
            <>
              <h2>模型解读</h2>
              <div className="panel">
                <div className="row">
                  <span className="pill unverified">候选 · 未验证</span>
                  {view.narrative.model && <span className="pill">{view.narrative.model}</span>}
                  {view.narrative.latency_ms != null && (
                    <span className="pill">{view.narrative.latency_ms} ms</span>
                  )}
                </div>
                <div className="answer" style={{ marginTop: 8 }}>
                  {view.narrative.candidate.draft}
                </div>
                <div className="sub" style={{ marginTop: 8, marginBottom: 0 }}>
                  标注：{view.narrative.candidate.verification}　｜　{String(view.narrative.candidate.boundary ?? "")}
                </div>
                {view.narrative.checks && (
                  <div className="row" style={{ marginTop: 8 }}>
                    {(["structure", "facts", "boundary", "label"] as const).map((key) => (
                      <span
                        key={key}
                        className="pill"
                        style={{
                          borderColor: view.narrative?.checks?.[key] ? "var(--ok)" : "var(--cinnabar)",
                          color: view.narrative?.checks?.[key] ? "var(--ok)" : "var(--cinnabar)"
                        }}
                      >
                        {key === "structure" ? "结构" : key === "facts" ? "事实" : key === "boundary" ? "越界" : "标注"}
                        {" "}
                        {view.narrative?.checks?.[key] ? "通过" : "拦截"}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {Object.keys(view.fields || {}).length > 0 ? (
            <>
          <h2>字段</h2>
          <p className="sub">按中文名列出判分字段；括号内是机器字段名（与接口返回一一对应）。</p>
          <table>
            <thead>
              <tr>
                <th style={{ width: 200 }}>字段</th>
                <th style={{ width: 80 }}>值</th>
                <th>含义</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(view.fields).map(([key, value]) => {
                const meta = view.field_meta?.[key];
                return (
                  <tr key={key}>
                    <td>
                      <b>{meta?.label ?? key}</b>
                      <div className="mono muted" style={{ fontSize: 11, marginTop: 2 }}>{key}</div>
                    </td>
                    <td className="num">{Array.isArray(value) ? value.join("、") : String(value)}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{meta?.desc ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
            </>
          ) : (
            <div className="note" style={{ marginTop: 10 }}>
              本次没有确定性字段——这类题超出了规则覆盖，答案由模型依据事实卡给出，不作为判分依据。
            </div>
          )}

          {view.trace.length > 0 && (
            <>
          <h2>计算链</h2>
          {view.trace.map((step, index) => (
            <details key={step.step} open={index === 0} className="panel" style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer" }}>
                {index + 1}. {step.step}
              </summary>
              <div className="kv" style={{ marginTop: 8 }}>
                <span>输入</span>
                <b>{step.input}</b>
              </div>
              <div className="kv">
                <span>公式</span>
                <b>{step.formula}</b>
              </div>
              <div className="kv">
                <span>输出</span>
                <b className="mono">{JSON.stringify(step.output)}</b>
              </div>
              <div className="kv">
                <span>证据</span>
                <b className="mono">{step.evidence_ids.join(" / ")}</b>
              </div>
            </details>
          ))}
            </>
          )}

          {view.solve_id && (
            <>
              <h2>推理链留档</h2>
              <div className="panel">
                <div className="row">
                  <span className="mono muted">solve_id</span>
                  <b className="mono">{view.solve_id}</b>
                  <button onClick={() => reloadTrace(view.solve_id!)}>重新取回</button>
                </div>
                <div className="muted" style={{ marginTop: 6 }}>
                  该次求解的推理链与字段已按 solve_id 留档（服务内保留最近 50 次），可按 ID 单独取回。
                </div>
                {traceError && <div className="error" style={{ marginTop: 8 }}>{traceError}</div>}
                {trace && (
                  <div style={{ marginTop: 10 }}>
                    <div className="row">
                      <span className="pill">{trace.trace.length} 步</span>
                      <span className="pill">{trace.evidence.length} 条证据</span>
                      {trace.category && <span className="pill">类别 {trace.category}</span>}
                    </div>
                    <ol style={{ margin: "8px 0 0", paddingLeft: 20 }}>
                      {trace.trace.map((step) => (
                        <li key={step.step}>
                          <b>{step.step}</b>
                          <span className="muted mono"> · {step.formula}</span>
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
              </div>
            </>
          )}

          <h2>证据</h2>
          {view.evidence.map((item) => (
            <div className="panel" key={item.work_id}>
              <div className="row">
                <b>
                  {item.title}（{item.work_id}）
                </b>
                <span className="pill verified">{item.tier_label}</span>
                <span className="pill">{item.source}</span>
                <button
                  onClick={() =>
                    navigate(
                      `/work?id=${item.work_id}${item.line_ids.length ? `&line=${item.line_ids[0]}` : ""}`
                    )
                  }
                >
                  看原句
                </button>
              </div>
              <div className="kv" style={{ marginTop: 8 }}>
                <span>句序</span>
                <b className="mono">{item.line_ids.join("、")}</b>
              </div>
              {item.quote && (
                <div className="kv">
                  <span>引文（第 {item.quote_line_no} 句）</span>
                  <b>{item.quote}</b>
                </div>
              )}
              {item.segments && item.segments.length > 0 && (
                <div className="kv" style={{ alignItems: "flex-start" }}>
                  <span>分段引文</span>
                  <b style={{ display: "grid", gap: 4, justifyItems: "end" }}>
                    {item.segments.map((segment) => (
                      <span key={segment.segment}>
                        <span className="muted">{segment.segment}第 {segment.quote_line_no} 句</span>　
                        {segment.quote}
                      </span>
                    ))}
                  </b>
                </div>
              )}
              {item.segments?.some((segment) => (segment.candidates?.length ?? 0) > 1) && (
                <div className="note">
                  同一段内还有可定位候选（按句长降序，供挑更贴题的一句；不代表语义归类）：
                  <div className="mono muted" style={{ marginTop: 6 }}>
                    {item.segments
                      .flatMap((segment) =>
                        (segment.candidates ?? []).slice(1).map((c) => `${segment.segment}第${c.line_no}句 ${c.text}`)
                      )
                      .join("　")}
                  </div>
                </div>
              )}
              {item.candidates && item.candidates.length > 1 && (
                <div className="note">
                  候选引文 {item.candidates.length} 条，默认取最长句；官方判分只要求可定位。
                  <div className="mono muted" style={{ marginTop: 6 }}>
                    {item.candidates.slice(0, 3).map((c) => `第${c.line_no}句 ${c.text}`).join("　")}
                  </div>
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </section>
  );
}

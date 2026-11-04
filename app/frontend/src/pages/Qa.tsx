import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import WorkPicker from "../WorkPicker";

type FactCard = {
  work: { id: number | string; title: string; tune: string; poet: string; period: string };
  facts: Record<string, string | number | number[]>;
  derived: Record<string, string | number>;
  quote_candidates: { line_no: number; text: string }[];
  source: string;
  tier_label: string;
};

type Candidate = {
  draft: string;
  basis: string[] | string;
  verification: string | null;
  boundary: string | null;
};

type Checks = { structure: boolean; facts: boolean; boundary: boolean; label: boolean };

type CandidatesResponse = {
  qa_id: string;
  state: "running" | "done" | "failed" | "cancelled";
  status: "ok" | "blocked" | "degraded" | null;
  candidate: Candidate | null;
  checks: Checks | null;
  violations: Record<string, unknown> | null;
  model: string | null;
  latency_ms: number | null;
  fallback_reason: string | null;
  error: { code: string; message: string } | null;
};

/** 一轮对话：用户问什么 + 助手回了什么。 */
type Turn = {
  key: string;
  question: string;
  workId: string;
  card: FactCard | null;
  response: CandidatesResponse | null;
  error: string | null;
};

const FACT_LABEL: Record<string, string> = {
  lines: "句数",
  characters: "总字数",
  ping: "平声",
  ze: "仄声",
  ze_ratio: "仄声比例",
  longest_line_nos: "最长句序",
  longest_chars: "最长句字数",
  split_after_line: "分段位置",
  pre_ratio: "前段比例",
  post_ratio: "后段比例",
  change: "变化值"
};

const SAMPLE_QUESTIONS = [
  "这首词的形式特征是什么？",
  "上下阕的仄声比例有什么变化？",
  "它的句式节奏有什么可核对的特点？",
  "平仄分布能看出什么样的情绪倾向？"
];

/** 模型引用的字段名 → 中文（模型给的是 facts.xxx 这样的机器名） */
const BASIS_LABEL: Record<string, string> = {
  "facts.lines": "句数",
  "facts.characters": "总字数",
  "facts.ping": "平声",
  "facts.ze": "仄声",
  "facts.ze_ratio": "仄声比例",
  "facts.pre_ratio": "前段比例",
  "facts.post_ratio": "后段比例",
  "facts.change": "变化值",
  "facts.longest_line_nos": "最长句序",
  "facts.longest_chars": "最长句字数",
  "facts.split_after_line": "分段位置"
};

function basisText(basis: string[] | string): string {
  const items = Array.isArray(basis) ? basis : [String(basis)];
  return items.map((item) => BASIS_LABEL[item] ?? item).join("、");
}

const CHECK_NAME: Record<keyof Checks, string> = {
  structure: "结构",
  facts: "事实",
  boundary: "越界",
  label: "标注"
};

export default function Qa() {
  const [params] = useSearchParams();
  const [workId, setWorkId] = useState(params.get("work") ?? "58633");
  const [question, setQuestion] = useState(params.get("question") ?? "");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const timer = useRef<number | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  // 新回合到达时把对话流滚到底
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  function patch(key: string, values: Partial<Turn>) {
    setTurns((prev) => prev.map((turn) => (turn.key === key ? { ...turn, ...values } : turn)));
  }

  async function ask(text?: string, model?: string) {
    const asked = (text ?? question).trim();
    const id = workId.trim();
    if (!asked) {
      setError("先写下你的问题");
      return;
    }
    if (!id) {
      setError("先选一首词");
      return;
    }
    setPending(true);
    setError(null);
    setQuestion("");
    const key = "turn-" + Date.now() + "-" + Math.floor(Math.random() * 1000);
    setTurns((prev) => [
      ...prev,
      { key, question: asked, workId: id, card: null, response: null, error: null }
    ]);

    try {
      const started = await fetch("/api/qa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ work_id: id, question: asked, model })
      });
      const body = await started.json();
      if (!body.ok) throw new Error(body.error?.message ?? "启动失败");
      patch(key, { card: body.data.fact_card as FactCard });
      poll(key, body.data.qa_id as string);
    } catch (err) {
      const message = err instanceof Error ? err.message : "启动失败";
      patch(key, { error: message });
      setError(message);
      setPending(false);
    }
  }

  function poll(key: string, qaId: string) {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      try {
        const res = await fetch(`/api/qa/${qaId}/candidates`);
        const body = await res.json();
        if (!body.ok) throw new Error(body.error?.message ?? "查询失败");
        const data = body.data as CandidatesResponse;
        patch(key, { response: data });
        if (data.state !== "running") {
          if (timer.current) window.clearInterval(timer.current);
          setPending(false);
        }
      } catch (err) {
        if (timer.current) window.clearInterval(timer.current);
        const message = err instanceof Error ? err.message : "查询失败";
        patch(key, { error: message });
        setError(message);
        setPending(false);
      }
    }, 600);
  }

  const empty = turns.length === 0;

  return (
    <section className="chat-page">
      <h1>问词</h1>
      <p className="sub">
        这是本系统的智能体入口。引擎先按官方标注算出事实卡，模型只在事实之上写候选解读；
        草稿要过四道闸门才展示，并始终标「未验证」。
      </p>

      <div className="chat-layout">
        <div className="chat-log" ref={logRef}>
          {empty && (
            <div className="empty">
              <div className="empty-mark" aria-hidden="true">問</div>
              <h3>问一句试试</h3>
              <p className="sub" style={{ marginBottom: 10 }}>
                先在下面选一首词，然后直接提问。可以问形式特征、上下阕的平仄变化、句式节奏——
                回答里的每个数字都能回到原句与计算记录。
              </p>
              <div className="row">
                {SAMPLE_QUESTIONS.map((item) => (
                  <button key={item} className="chip" onClick={() => ask(item)} disabled={pending}>
                    {item}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn) => (
            <div key={turn.key} className="turn">
              <div className="bubble user">
                <div className="bubble-meta">你 · {turn.workId ? `作品 ${turn.workId}` : ""}</div>
                <div>{turn.question}</div>
              </div>

              <div className="bubble bot">
                <div className="bubble-meta">词律探微</div>
                {turn.error && <div className="error">{turn.error}</div>}

                {turn.card && (
                  <>
                    <div className="fact-card">
                      <div className="row">
                        <b style={{ fontFamily: "var(--font-song)", fontSize: 15 }}>
                          {turn.card.work.title}·{turn.card.work.tune}
                        </b>
                        <span className="muted">
                          {turn.card.work.poet} · {turn.card.work.period}
                        </span>
                        <span className="pill verified">{turn.card.tier_label}</span>
                      </div>
                      <div className="fact-grid">
                        {Object.entries(turn.card.facts).map(([key, value]) => (
                          <div className="kv" key={key}>
                            <span>{FACT_LABEL[key] ?? key}</span>
                            <b>{Array.isArray(value) ? value.join("、") : String(value)}</b>
                          </div>
                        ))}
                      </div>
                      {turn.card.quote_candidates.length > 0 && (
                        <details className="muted" style={{ marginTop: 6 }}>
                          <summary style={{ cursor: "pointer", fontSize: 12 }}>
                            后段候选引文（{turn.card.quote_candidates.length} 条）
                          </summary>
                          <div className="mono muted" style={{ fontSize: 12 }}>
                            {turn.card.quote_candidates.map((item) => (
                              <div key={item.line_no}>
                                第 {item.line_no} 句　{item.text}
                              </div>
                            ))}
                          </div>
                        </details>
                      )}
                    </div>
                    <div className="bubble-note">事实卡由引擎给出，可复算；下面的解读只是候选。</div>
                  </>
                )}

                {turn.response?.state === "running" && (
                  <div className="muted">模型生成中，通常 2–4 秒…</div>
                )}

                {turn.response?.status === "ok" && turn.response.candidate && (
                  <>
                    <div className="answer" style={{ marginTop: 8 }}>
                      {turn.response.candidate.draft}
                    </div>
                    <div className="sub" style={{ marginTop: 8, marginBottom: 0 }}>
                      标注：{turn.response.candidate.verification}　｜　{String(turn.response.candidate.boundary ?? "")}
                    </div>
                    <div className="kv" style={{ marginTop: 8 }}>
                      <span>依据字段</span>
                      <b>{basisText(turn.response.candidate.basis)}</b>
                    </div>
                  </>
                )}

                {turn.response?.status === "blocked" && (
                  <div className="error" style={{ marginTop: 8 }}>
                    草稿未通过闸门，不予展示。
                    <div className="mono" style={{ marginTop: 6 }}>
                      {JSON.stringify(turn.response.violations)}
                    </div>
                  </div>
                )}

                {turn.response?.status === "degraded" && (
                  <div className="note" style={{ marginTop: 8 }}>
                    模型未提供候选解释，上面的事实卡仍然可用。原因：{turn.response.fallback_reason ?? "模型不可用"}
                  </div>
                )}

                {turn.response?.checks && (
                  <div className="row" style={{ marginTop: 10 }}>
                    {(Object.keys(CHECK_NAME) as (keyof Checks)[]).map((key) => {
                      const ok = turn.response?.checks?.[key];
                      return (
                        <span
                          key={key}
                          className="pill"
                          style={{
                            borderColor: ok ? "var(--ok)" : "var(--cinnabar)",
                            color: ok ? "var(--ok)" : "var(--cinnabar)"
                          }}
                        >
                          {CHECK_NAME[key]} {ok ? "通过" : "拦截"}
                        </span>
                      );
                    })}
                    {turn.response.model && <span className="pill">{turn.response.model}</span>}
                    {turn.response.latency_ms != null && (
                      <span className="pill">{turn.response.latency_ms} ms</span>
                    )}
                  </div>
                )}

                {turn.response?.error && (
                  <div className="error" style={{ marginTop: 8 }}>
                    {turn.response.error.code}：{turn.response.error.message}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="chat-input">
          <div className="row">
            <WorkPicker value={workId} onChange={setWorkId} />
            <input
              className="field"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") ask();
              }}
              placeholder="问一句：这首词的上下阕平仄有什么变化？"
              aria-label="问题"
              style={{ flex: 1, minWidth: 220 }}
            />
            <button onClick={() => ask()} disabled={pending}>
              {pending ? "生成中…" : "发送"}
            </button>
            <button onClick={() => ask(undefined, "step-3.7-flash")} disabled={pending} title="换第二个模型再答一次">
              换 flash
            </button>
          </div>
          {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
          <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
            事实闸门只能验证「数字能在事实卡里找到出处」，不能验证「数字被放在了正确的字段上」；
            所以候选一律标未验证，需要人对照事实卡核对。
          </div>
        </div>
      </div>
    </section>
  );
}

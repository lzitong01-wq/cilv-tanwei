import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type FactCard, type WorkSummary } from "../api";

const FACT_LABEL: Record<string, string> = {
  lines: "句数",
  characters: "总字数",
  ping: "平声字数",
  ze: "仄声字数",
  ze_ratio: "全篇仄声比例",
  longest_line_nos: "最长句句序",
  longest_chars: "最长句字数",
  split_after_line: "官方分段位置",
  pre_ratio: "前段仄声比例",
  post_ratio: "后段仄声比例",
  change: "前后段变化"
};

const BOUNDARY =
  "以上仅为可复算的声律事实与文本片段，不构成对作品情感、文学价值、作者经历或时代因果的判断；" +
  "景物／感受归类与声律—语义互证没有经验证的语义金标准，不作为结论。语义验证状态：未验证。";

const CLAIM_TEMPLATE = "就形式特征而言，这首词的句长与前后段仄声比例可作如下对照。";

type EvidenceCard = {
  id: string;
  label: string;
  text: string;
  ref: string;
  tier: string;
  line_no?: number;
};

// 事实卡整体以官方 prosody 为输入，但比例、最长句、变化值等是引擎派生，
// 不能统统标成「官方标注」。草稿里要能一眼看出哪些是原始计数、哪些是算出来的。
const DERIVED_KEYS = new Set([
  "ze_ratio", "longest_line_nos", "longest_chars", "pre_ratio", "post_ratio", "change"
]);

function buildCards(card: FactCard): EvidenceCard[] {
  const items: EvidenceCard[] = Object.entries(card.facts).map(([key, value]) => ({
    id: `facts.${key}`,
    label: FACT_LABEL[key] ?? key,
    text: Array.isArray(value) ? value.join("、") : String(value),
    ref: `facts.${key}`,
    tier: DERIVED_KEYS.has(key) ? `引擎派生 · ${card.tier_label}口径` : card.tier_label
  }));
  for (const quote of card.quote_candidates) {
    items.push({
      id: `quote.${quote.line_no}`,
      label: `后段第 ${quote.line_no} 句`,
      text: quote.text,
      ref: `line:${quote.line_no}`,
      tier: "原文",
      line_no: quote.line_no
    });
  }
  return items;
}

export default function Argument() {
  const navigate = useNavigate();
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [workId, setWorkId] = useState("58633");
  const [card, setCard] = useState<FactCard | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [claim, setClaim] = useState(CLAIM_TEMPLATE);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // 模型生成的论证草稿（null = 还在用模板拼装）
  const [modelDraft, setModelDraft] = useState<string | null>(null);
  const [draftPending, setDraftPending] = useState(false);
  const [draftChecks, setDraftChecks] = useState<Record<string, boolean> | null>(null);
  const [draftVerification, setDraftVerification] = useState("");

  useEffect(() => {
    api
      .works({ page: 1, page_size: 100 })
      .then((data) => setWorks(data.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .factCard(Number(workId))
      .then((data) => {
        if (!alive) return;
        setCard(data);
        const cards = buildCards(data);
        setSelected(new Set(cards.slice(0, 6).map((item) => item.id)));
      })
      .catch((err: Error) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, [workId]);

  const cards = useMemo(() => (card ? buildCards(card) : []), [card]);
  const chosen = cards.filter((item) => selected.has(item.id));

  const draft = useMemo(() => {
    const lines = [
      "【主张】",
      claim.trim() || CLAIM_TEMPLATE,
      "",
      "【证据】",
      ...chosen.map(
        (item, index) =>
          `${index + 1}. ${item.label}：${item.text}　[${item.ref} · ${item.tier}]`
      ),
      ...(chosen.length === 0 ? ["（尚未选择证据，草稿不成立）"] : []),
      "",
      "【限定】",
      BOUNDARY,
      "",
      `作品：${card?.work.title ?? "—"}（#${card?.work.id ?? "—"}，${card?.work.poet ?? "—"}）`,
      `事实卡来源：${card?.source ?? "—"}；${modelDraft ? "草稿由模型依据勾选证据生成，标未验证。" : "草稿由模板拼装，未经模型生成。"}`
    ];
    return lines.join("\n");
  }, [claim, chosen, card]);

  // 有模型草稿就用它，否则回落到模板拼装（断网/模型不可用时的诚实降级）
  const activeDraft = modelDraft ?? draft;

  async function generateDraft() {
    if (!card || chosen.length === 0) {
      setError("先勾选至少一条证据再生成");
      return;
    }
    setDraftPending(true);
    setError(null);
    try {
      const response = await fetch("/api/argument/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          work_id: card.work.id,
          claim: claim.trim() || CLAIM_TEMPLATE,
          refs: chosen.map((item) => item.ref)
        })
      });
      const body = await response.json();
      if (!body.ok) throw new Error(body.error?.message ?? "生成失败");
      const data = body.data;
      if (data.status === "degraded" || !data.candidate?.draft) {
        // 不把降级结果伪装成模型草稿，回落到模板并如实提示
        setModelDraft(null);
        setDraftChecks(null);
        setError("模型不可用，已回落到模板拼装草稿（未生成模型草稿）");
      } else {
        setModelDraft(
          `【模型草稿 · ${data.candidate.verification || "未验证"}】\n${data.candidate.draft}\n\n` +
          `【依据】${(data.candidate.basis || []).join("、") || "—"}\n` +
          `【限定】${data.candidate.boundary || "不推断作者生平、情感归类与作品优劣。"}`
        );
        setDraftChecks(data.checks ?? null);
        setDraftVerification(data.candidate.verification || "未验证");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败");
    } finally {
      setDraftPending(false);
    }
  }

  function toggle(id: string) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function download() {
    const blob = new Blob(["\ufeff" + activeDraft], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `论证草稿_${card?.work.id ?? "x"}.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section>
      <h1>论证辅助</h1>
      <p className="sub">
        把「主张 — 证据 — 限定」拼成一段可核对的草稿。证据卡来自引擎事实卡，逐条可插拔；
        草稿由模板拼装，<b>不调用模型</b>，模型断连时这一页照常可用。
      </p>

      <div className="row">
        <select
          className="field"
          value={workId}
          onChange={(event) => setWorkId(event.target.value)}
          aria-label="选择作品"
          style={{ maxWidth: 280 }}
        >
          {!works.some((work) => String(work.id) === workId) && <option value={workId}>作品 #{workId}</option>}
          {works.map((work) => (
            <option key={work.id} value={work.id}>
              {work.title}·{work.tune}｜{work.poet}
            </option>
          ))}
        </select>
        {card && (
          <>
            <span className="pill verified">{card.tier_label}</span>
            <span className="muted">
              {card.work.title} · {card.work.period} · 共 {String(card.facts.lines)} 句
            </span>
          </>
        )}
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      <div className="split-2" style={{ marginTop: 14 }}>
        <div>
          <h2>证据卡（点选加入 / 移出草稿）</h2>
          {cards.length === 0 ? (
            <div className="panel muted">正在载入事实卡…</div>
          ) : (
            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))" }}>
              {cards.map((item) => (
                <button
                  key={item.id}
                  className="case"
                  onClick={() => toggle(item.id)}
                  aria-pressed={selected.has(item.id)}
                  style={{
                    textAlign: "left",
                    borderTopColor: selected.has(item.id) ? "var(--cinnabar)" : "var(--rule)",
                    opacity: selected.has(item.id) ? 1 : 0.72
                  }}
                >
                  <div>
                    <h3 style={{ fontSize: 12 }}>{item.label}</h3>
                    <p className="mono" style={{ color: "var(--ink)", fontSize: 12 }}>{item.text}</p>
                    <p style={{ fontSize: 10 }}>{item.ref} · {item.tier}</p>
                  </div>
                  <span className="pill">{selected.has(item.id) ? "已纳入" : "未纳入"}</span>
                </button>
              ))}
            </div>
          )}
          {card?.quote_candidates[0]?.line_no && (
            <div className="row" style={{ marginTop: 10 }}>
              <button
                className="chip"
                onClick={() => navigate(`/work?id=${card.work.id}&line=${card.quote_candidates[0].line_no}`)}
              >
                看第 {card.quote_candidates[0].line_no} 句原文
              </button>
              <span className="muted">每条结论都能回跳到原句</span>
            </div>
          )}
        </div>

        <aside>
          <div className="panel">
            <div className="sub" style={{ marginBottom: 8 }}>主张（可改）</div>
            <textarea
              value={claim}
              onChange={(event) => setClaim(event.target.value)}
              aria-label="主张"
              style={{ minHeight: 78, fontSize: 12 }}
            />
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={() => setClaim(CLAIM_TEMPLATE)}>重置为模板</button>
              <span className="muted mono">{chosen.length} 条证据</span>
            </div>
          </div>
          <div className="panel">
            <div className="sub" style={{ marginBottom: 8 }}>
              {modelDraft ? `草稿（模型生成 · ${draftVerification}）` : "草稿（模板拼装）"}
              {draftChecks && (
                <span className="muted mono" style={{ marginLeft: 8 }}>
                  闸门 {Object.entries(draftChecks).map(([key, ok]) => `${key}:${ok ? "✓" : "✗"}`).join(" ")}
                </span>
              )}
            </div>
            <pre className="answer" style={{ fontSize: 12, maxHeight: 420, overflow: "auto" }}>{activeDraft}</pre>
            <div className="row" style={{ marginTop: 10 }}>
              <button onClick={() => void generateDraft()} disabled={draftPending || chosen.length === 0}>
                {draftPending ? "生成中…" : "生成论证草稿"}
              </button>
              {modelDraft && <button onClick={() => { setModelDraft(null); setDraftChecks(null); }}>回到模板</button>}
              <button
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(activeDraft);
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1500);
                  } catch {
                    setError("浏览器未允许复制，请用下方导出");
                  }
                }}
              >
                {copied ? "已复制" : "复制草稿"}
              </button>
              <button onClick={download} disabled={chosen.length === 0}>导出 txt</button>
            </div>
          </div>
          <div className="note">限定段固定携带，不可删除：不推断作者生平、情感归类与作品优劣。</div>
        </aside>
      </div>
    </section>
  );
}

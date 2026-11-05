import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type DemoCaseSet, type Meta } from "../api";

export default function Home() {
  const navigate = useNavigate();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [cases, setCases] = useState<DemoCaseSet | null>(null);
  const [casesMissing, setCasesMissing] = useState(false);
  const [askText, setAskText] = useState("");

  useEffect(() => {
    // 健康状态由 App 顶层统一取一次并在顶栏展示，这里不再重复请求
    api.meta().then(setMeta).catch(() => undefined);
    // 三个演示案例，由构建期脚本用真实引擎生成回放数据
    api
      .demoCases()
      .then(setCases)
      .catch(() => setCasesMissing(true));
  }, []);

  function askNow(text?: string) {
    const q = (text ?? askText).trim();
    if (!q) return;
    navigate(`/qa?question=${encodeURIComponent(q)}`);
  }

  async function loadDemo() {
    setLoadingDemo(true);
    setError(null);
    try {
      const demo = await api.demo();
      navigate("/solve", { state: { question: demo.question, category: demo.category } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "示例题目不可用");
    } finally {
      setLoadingDemo(false);
    }
  }

  function useCase(id: string, replay = false) {
    const item = cases?.cases.find((caseItem) => caseItem.id === id);
    if (!item) return;
    if (item.mode === "revision") {
      navigate(item.target);
      return;
    }
    navigate(item.target, {
      state: { text: item.text ?? "", tune: item.tune ?? "", caseId: item.id, replay }
    });
  }

  function caseSummary(cached: Record<string, unknown>) {
    if (typeof cached.line_count === "number") {
      return `${cached.line_count} 句 · 预计算${cached.ambiguous ? ` · 同调 ${cached.hint_form_count} 种句式` : ""}`;
    }
    const stats = cached.stats as { total?: number; by_kind?: Record<string, number> } | undefined;
    return stats ? `共 ${stats.total} 条差异 · ${stats.by_kind?.polyphone ?? 0} 条可用多音字解释` : "校订闭环";
  }

  const periods = meta
    ? Object.entries(meta.periods)
        .map(([name, count]) => `${name} ${count}`)
        .join(" / ")
    : "—";

  return (
    <section>
      <h1>看清一首词的声律，再谈它的声情</h1>
      <p className="sub">
        声律事实由确定性引擎给出，每一条都能回到原句与计算记录；模型只写候选，且必须标注未验证。
      </p>

      {/* 智能体入口：首屏第一件事就是能提问 */}
      <div className="ask-box">
        <div className="ask-box-label">向词律探微提问</div>
        <div className="row">
          <input
            className="field ask-input"
            value={askText}
            onChange={(event) => setAskText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") askNow();
            }}
            placeholder="例如：纳兰《长相思》的上下阕平仄比例有什么变化？"
            aria-label="向词律探微提问"
          />
          <button className="ask-submit" onClick={() => askNow()} disabled={!askText.trim()}>
            提问 →
          </button>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted">试试：</span>
          {[
            "这首词的上下阕平仄比例有什么变化？",
            "纳兰《长相思》的声情可以从哪些形式特征上核对？",
            "帮我比较两首同调词的仄声比例"
          ].map((item) => (
            <button key={item} className="chip" onClick={() => askNow(item)}>
              {item}
            </button>
          ))}
        </div>
      </div>

      <div className="grid">
        <div className="stat">
          <span>语料总量</span>
          <strong>{meta?.corpus_total ? meta.corpus_total.toLocaleString() : "—"}</strong>
          <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
            官方标注 {meta?.works ?? "—"} 篇 + 同源公开整理本 {meta?.tier2?.works?.toLocaleString() ?? "—"} 首
          </div>
        </div>
        <div className="stat">
          <span>词人 / 词牌</span>
          <strong>
            {meta?.tier2
              ? `${(meta.tier2.poets + (meta.poets || 0)).toLocaleString()} / ${meta.tier2.tunes.toLocaleString()}`
              : "—"}
          </strong>
          <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>三层语料合计</div>
        </div>
        <div className="stat">
          <span>官方语料朝代</span>
          <strong style={{ fontSize: 15 }}>{periods}</strong>
        </div>
        <div className="stat">
          <span>官方标注</span>
          <strong style={{ fontSize: 15 }}>{meta ? `${meta.works} 首` : "—"}</strong>
          <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>唯一基准口径</div>
        </div>
      </div>

      {error && (
        <div className="error" style={{ marginTop: 14 }}>
          {error}
        </div>
      )}

      <h2>三个真实案例</h2>
      {casesMissing ? (
        <div className="panel muted">
          预计算演示数据未生成。运行 <span className="mono">python app/scripts/build_demo_cases.py</span> 后，
          这里会出现三个可一键复跑的案例。
        </div>
      ) : (
        <div className="cases">
          {(cases?.cases ?? []).map((item) => (
            <div className="case" key={item.id}>
              <div>
                <h3>{item.title}</h3>
                <p>{item.caption}</p>
                <p className="mono" style={{ marginTop: 5, color: "var(--ink-3)" }}>
                  {caseSummary(item.cached)}
                </p>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <button className="chip" onClick={() => useCase(item.id)}>
                  {item.mode === "revision" ? "看闭环 →" : "现场计算 →"}
                </button>
                {item.mode === "parse" && (
                  <button className="chip" onClick={() => useCase(item.id, true)} title="展示构建期预计算结果">
                    回放
                  </button>
                )}
              </div>
            </div>
          ))}
          {!cases && <div className="case muted">正在载入演示案例…</div>}
        </div>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button className="chip" onClick={() => navigate("/library")}>
          书库 · 按书影浏览 {meta?.corpus_total?.toLocaleString() ?? "—"} 首 →
        </button>
        <button className="chip" onClick={() => navigate("/style")}>
          风格对比 · 看两位词人的形式差异 →
        </button>
        <button className="chip" onClick={() => navigate("/about")}>
          语料从哪来 · 授权说明 →
        </button>
      </div>

      <h2>三步走</h2>
      <div className="panel">
        <div className="kv">
          <span>一、粘贴一道题面</span>
          <b>识别类别与作品锚点</b>
        </div>
        <div className="kv">
          <span>二、看引擎给出的字段</span>
          <b>句数、最长句、平仄、比例、差值</b>
        </div>
        <div className="kv">
          <span>三、展开计算链与证据</span>
          <b>每一步都能回到原句</b>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button onClick={loadDemo} disabled={loadingDemo}>
            {loadingDemo ? "载入中…" : "载入一道示例题"}
          </button>
          <button onClick={() => navigate("/work")}>先看一篇词</button>
        </div>
        <p className="sub" style={{ marginTop: 10, marginBottom: 0 }}>
          示例题面从本地题库读取，不写入前端产物；题库不在位时该按钮会明确报错。
        </p>
      </div>

      <h2>这个系统能做什么、不能做什么</h2>
      <div className="panel">
        <div className="kv">
          <span>能算准的</span>
          <b>五类形式指标：全篇仄声比例、前后半段变化、长句仄声密度、后段排序、景情节奏</b>
        </div>
        <div className="kv">
          <span>不能替你做的</span>
          <b>判断作品优劣、推断作者生平、断言两首词的情感高低</b>
        </div>
        <div className="kv">
          <span>四声从哪来</span>
          <b>官方只给逐句平仄计数，逐字四声是本地字典算的，非官方标注</b>
        </div>
        <div className="kv">
          <span>模型写的东西</span>
          <b>只出候选解读，四道闸门全过才显示，一律标「未验证」，要你自己核对</b>
        </div>
        <div className="kv">
          <span>没有的部分</span>
          <b>词人生平与历史事件还没有开放数据，图谱里标着「待发布」，不用推测补全</b>
        </div>
      </div>
    </section>
  );
}

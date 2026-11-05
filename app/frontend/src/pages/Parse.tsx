import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, type DemoCaseSet, type ParseResult } from "../api";

type Preset = { text?: string; tune?: string; caseId?: string; replay?: boolean } | null;

export default function Parse() {
  const location = useLocation();
  const navigate = useNavigate();
  const preset = (location.state as Preset) ?? null;
  const [text, setText] = useState(preset?.text ?? "谁念西风独自凉。");
  const [tune, setTune] = useState(preset?.tune ?? "");
  const [result, setResult] = useState<ParseResult | null>(null);
  const [caseSet, setCaseSet] = useState<DemoCaseSet | null>(null);
  const [activeCase, setActiveCase] = useState<string | null>(preset?.caseId ?? null);
  const [replay, setReplay] = useState(Boolean(preset?.replay));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .demoCases()
      .then(setCaseSet)
      .catch(() => undefined);
  }, []);

  async function run(value = text, tuneHint = tune) {
    setPending(true);
    setError(null);
    try {
      setResult(await api.parse(value, tuneHint));
      setReplay(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "解析失败");
      setResult(null);
    } finally {
      setPending(false);
    }
  }

  // 首屏就直接出一次真实结果；从首页案例跳进来时用案例文本。
  // 只有明确要求"预计算回放"时才不触发现场计算，避免把回放态覆盖掉。
  useEffect(() => {
    if (preset?.replay) return;
    void run(preset?.text ?? "谁念西风独自凉。", preset?.tune ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function useCase(id: string) {
    const item = caseSet?.cases.find((caseItem) => caseItem.id === id);
    if (!item) return;
    setActiveCase(id);
    if (item.mode === "revision") {
      navigate(item.target);
      return;
    }
    setText(item.text ?? "");
    setTune(item.tune ?? "");
    void run(item.text ?? "", item.tune ?? "");
  }

  const cached = caseSet?.cases.find((item) => item.id === activeCase)?.cached;

  return (
    <section>
      <h1>解析新词</h1>
      <p className="sub">
        词文不在官方 423 篇里时也能逐字解析。以下结果全部是本地计算口径，不是官方标注；
        体式候选只比对库内已见句式，不引入词谱，也不代替人工归体。
      </p>

      <div className="row">
        <span className="pill unverified">非官方口径 · 本地计算</span>
        <span className="muted">句末用句号／问号／叹号；句内逗号保留</span>
        <div className="spacer" />
        <button className={!replay ? "chip active-chip" : "chip"} onClick={() => setReplay(false)}>
          现场计算
        </button>
        <button
          className={replay ? "chip active-chip" : "chip"}
          onClick={() => setReplay(true)}
          disabled={!cached}
          title={cached ? "展示构建期预计算结果" : "先在上方选一个演示案例"}
        >
          预计算回放
        </button>
      </div>

      {caseSet && (
        <div className="row" style={{ marginTop: 10 }}>
          <span className="muted">定死的三个演示案例：</span>
          {caseSet.cases.map((item) => (
            <button
              key={item.id}
              className="chip"
              onClick={() => useCase(item.id)}
              title={item.caption}
            >
              {item.title}
            </button>
          ))}
        </div>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <input
          className="field"
          value={tune}
          onChange={(event) => setTune(event.target.value)}
          placeholder="词牌提示（可留空）"
          aria-label="词牌提示"
          style={{ width: 180 }}
        />
        <span className="muted mono">GEN {caseSet?.generated_at.slice(0, 10) ?? "—"}</span>
      </div>
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        aria-label="待解析词文"
        placeholder="粘贴词正文，例如：谁念西风独自凉。"
        style={{ marginTop: 8, minHeight: 100 }}
      />
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={() => void run()} disabled={pending}>
          {pending ? "解析中…" : "解析这段词文"}
        </button>
        <span className="muted">纯引擎，不调用模型；模型断连时此页照常可用</span>
      </div>

      {error && (
        <div className="error" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}

      {replay && cached && (
        <>
          <h2>预计算回放 · {caseSet?.cases.find((c) => c.id === activeCase)?.title}</h2>
          <div className="note">
            这是构建期用同一套引擎算好后冻结的摘要（生成于 {caseSet?.generated_at}，
            语料副本校验码前 16 位 {caseSet?.source_sha256}）。它不是现场计算，也不含逐字明细；
            要看逐字格阵请切回「现场计算」。
          </div>
          <div className="panel" style={{ marginTop: 10 }}>
            <div className="kv">
              <span>句数</span>
              <b>{String(cached.line_count ?? "—")}</b>
            </div>
            <div className="kv">
              <span>句式字数序列</span>
              <b className="mono">{JSON.stringify(cached.pattern ?? [])}</b>
            </div>
            <div className="kv">
              <span>平 / 仄（本地口径）</span>
              <b className="mono">
                {String(cached.ping_total ?? "—")} / {String(cached.ze_total ?? "—")}
              </b>
            </div>
            <div className="kv">
              <span>同调多句式 · 不能唯一定体</span>
              <b>{cached.ambiguous === true ? `是（${cached.hint_form_count} 种句式）` : "否"}</b>
            </div>
            <div className="kv">
              <span>未知字</span>
              <b>{String(cached.unknown_count ?? "—")}</b>
            </div>
            {cached.located != null && (
              <div className="kv">
                <span>库内定位</span>
                <b>#{(cached.located as { work_id: number }).work_id}</b>
              </div>
            )}
          </div>
        </>
      )}

      {!replay && result && (
        <>
          {result.located && (
            <div className="note" style={{ marginTop: 12 }}>
              {result.located.official ? (
                <>
                  这段词文与官方语料中的 <b>{result.located.title}</b>（#{result.located.work_id}，
                  {result.located.poet}）逐字一致。{result.located.hint}
                  <button className="chip" style={{ marginLeft: 8 }} onClick={() => navigate(`/work?id=${result.located?.work_id}`)}>
                    看官方口径
                  </button>
                </>
              ) : (
                <>
                  <span className="pill unverified">非官方口径</span>{" "}
                  这段词文不在官方 423 篇内，已在<b>{result.located.tier_label}</b>中定位到{" "}
                  <b>{result.located.title}</b>（{result.located.poet}）。
                  {result.located.warning}
                </>
              )}
            </div>
          )}

          <h2>逐字声律（本地计算）</h2>
          {result.rhyme_boundary && (
            <div className="note" style={{ marginBottom: 8 }}>
              韵脚：{result.rhyme_boundary}
            </div>
          )}
          <div className="row">
            <span className="mono muted">句式 {JSON.stringify(result.pattern)}</span>
            <span className="muted">分阕候选：第 {result.split_after} 句后（本地推测）</span>
            <span className="pill">未知字 {result.unknown_count}</span>
          </div>
          <div className="verses">
            {result.lines.map((line) => (
              <div key={line.line_no}>
                <div className="verse" role="group" aria-label={`第 ${line.line_no} 句`}>
                  {line.chars.map((char, index) => (
                    <span
                      key={index}
                      className={`ch ${char.ping_ze === "平" ? "ping" : char.ping_ze === "仄" ? "ze" : "unknown"}`}
                      title={`${char.pinyin || "未知"} · ${char.tone_name} · ${char.tier_label}`}
                    >
                      {char.char}
                      <u>{char.pinyin || "—"}</u>
                    </span>
                  ))}
                </div>
                <div className="mono muted" style={{ marginLeft: 4 }}>
                  第 {line.line_no} 句 · {line.character_count} 字 · 平 {line.ping_count} · 仄 {line.ze_count}
                  （本地计数）
                  {line.line_end?.line_end_char && (
                    <>
                      {" · 句末字 "}
                      <b>{line.line_end.line_end_char}</b>
                      {line.line_end.rhyme_found
                        ? ` · ${line.line_end.rhyme}`
                        : " · 韵部未收录"}
                    </>
                  )}
                </div>
                {line.line_no === result.split_after && <div className="split" aria-hidden="true"><i /><span className="mono">下阕（推测）</span><i /></div>}
              </div>
            ))}
          </div>

          {result.unknown.length > 0 && (
            <div className="note">
              未知字（不默认归仄）：
              {result.unknown.map((item, index) => (
                <span key={index} className="mono" style={{ marginLeft: 8 }}>
                  第{item.line_no}句 {item.char} — {item.reason}
                </span>
              ))}
            </div>
          )}

          <h2>体式候选</h2>
          {result.tunes.ambiguous && (
            <div className="note" style={{ borderLeftWidth: 3 }}>
              <b>不能唯一定体。</b>{result.tunes.boundary}
            </div>
          )}
          {result.tunes.candidates.length === 0 ? (
            <div className="panel">库内没有吻合的句式，保持「无法归体」，不硬凑词牌。</div>
          ) : (
            <table style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th style={{ width: 140 }}>词牌 · 体式</th>
                  <th style={{ width: 80 }}>吻合度</th>
                  <th style={{ width: 100 }}>置信度</th>
                  <th>依据 / 库内样例</th>
                </tr>
              </thead>
              <tbody>
                {result.tunes.candidates.map((item) => (
                  <tr key={item.form_id}>
                    <td>
                      <div>{item.tune}</div>
                      <div className="mono muted">{item.form_id}</div>
                    </td>
                    <td className="num">{item.score.toFixed(2)}</td>
                    <td>{item.confidence}</td>
                    <td>
                      <div>{item.reasons.join("；")}</div>
                      <div className="muted mono" style={{ marginTop: 3 }}>
                        句式 {JSON.stringify(item.pattern)} · 库内 {item.sample_count} 篇 ·{" "}
                        {item.sample_work_ids.map((id) => (
                          <button key={id} className="linkish" onClick={() => navigate(`/work?id=${id}`)}>
                            #{id}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="note" style={{ marginTop: 10 }}>{result.boundary}</div>
        </>
      )}
    </section>
  );
}

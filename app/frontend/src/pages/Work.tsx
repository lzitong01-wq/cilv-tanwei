import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type CharDoc, type CharItem, type ProsodyResolve, type WorkSummary } from "../api";

export default function Work() {
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [query, setQuery] = useState("");
  const [tier, setTier] = useState<"all" | "1" | "2a" | "2b">("all");
  const [counts, setCounts] = useState<{
    works: number;
    poets: number;
    tunes: number;
    layers?: { tier: string; label: string; works: number }[];
  } | null>(null);
  const [activeId, setActiveId] = useState<number | string | null>(null);
  const [doc, setDoc] = useState<CharDoc | null>(null);
  const [selected, setSelected] = useState<{ line: number; index: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prosody, setProsody] = useState<ProsodyResolve | null>(null);
  const [params, setParams] = useSearchParams();

  const rawId = params.get("id");
  // 同源层的 id 是 `t2a_` / `t2b_` 前缀的字符串；官方层是整数
  const deepId =
    rawId && rawId.startsWith("t2") ? rawId : Number(rawId) || null;
  // 从证据卡跳过来时带 ?line=，用于定位并脉冲标出原句
  const targetLine = Number(params.get("line")) || null;

  useEffect(() => {
    let alive = true;
    api
      .meta()
      .then((data) => alive && data.tier2 && setCounts(data.tier2))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    // 列表由服务端检索（4.3 万首装不进浏览器），输入停顿 250 ms 再查
    const timer = setTimeout(() => {
      api
        .works({ page: 1, page_size: 120, tier, q: query.trim() || undefined })
        .then(async (data) => {
          let items = data.items;
          // 图谱跳过来的作品可能不在当前结果里，单独补一条摘要，保证列表能高亮
          if (deepId && !items.some((work) => work.id === deepId)) {
            try {
              const one = await api.work(deepId);
              items = [
                {
                  id: one.id,
                  title: one.title,
                  tune: one.tune,
                  period: one.period,
                  poet: one.poet,
                  lines: 0,
                  tier_label: one.tier_label,
                  source: one.source
                },
                ...items
              ];
            } catch {
              // 深链作品不存在：保持默认，不阻断页面
            }
          }
          if (!alive) return;
          setWorks(items);
          const wanted = deepId && items.some((work) => work.id === deepId) ? deepId : null;
          if (wanted) setActiveId(wanted);
          else if (items.length > 0) setActiveId(items[0].id);
        })
        .catch((err: Error) => alive && setError(err.message));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // 深链只在进入页面时读一次，后续切换不重新拉列表
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tier, query]);

  const choose = (id: number | string) => {
    setActiveId(id);
    setParams({ id: String(id) }, { replace: true });
  };

  useEffect(() => {
    if (activeId == null) return;
    setDoc(null);
    setSelected(null);
    api
      .chars(activeId)
      .then(setDoc)
      .catch((err: Error) => setError(err.message));
    // 词谱体式候选：多候选 + 置信度，不给单一答案。
    // 同源层没有官方体式可比，不给候选——不拿外部字表冒充官方口径。
    if (typeof activeId === "string") {
      setProsody(null);
      return;
    }
    api
      .prosodyResolve(activeId)
      .then(setProsody)
      .catch(() => setProsody(null));
  }, [activeId]);

  useEffect(() => {
    if (!doc || !targetLine) return;
    const node = document.getElementById(`line-${targetLine}`);
    node?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [doc, targetLine]);

  const layerCount = (key: string) =>
    counts?.layers?.find((item) => item.tier.endsWith(key))?.works ?? 0;

  // 列表由服务端按关键词检索（4.3 万首装不进浏览器），这里只做兜底过滤
  const filtered = useMemo(() => {
    const keyword = query.trim();
    if (!keyword) return works;
    return works.filter(
      (work) =>
        work.title.includes(keyword) ||
        (work.poet ?? "").includes(keyword) ||
        (work.tune ?? "").includes(keyword)
    );
  }, [works, query]);

  const activeChar: CharItem | null = doc && selected ? doc.lines[selected.line]?.chars[selected.index] ?? null : null;

  return (
    <section>
      <h1>单篇解析</h1>
      <p className="sub">
        逐字平仄与四声为本地字典计算；带官方标注的那一层，逐句平仄计数直接来自官方。两者口径不同，分别标注。
      </p>

      <div className="row" style={{ marginBottom: 12 }}>
        <button
          className={tier === "all" ? "chip active-chip" : "chip"}
          onClick={() => setTier("all")}
          title="三层合并检索，官方标注优先"
        >
          全部 {counts ? `${(counts.works + 423).toLocaleString()} 首` : ""}
        </button>
        <button
          className={tier === "1" ? "chip active-chip" : "chip"}
          onClick={() => setTier("1")}
        >
          官方标注 {counts ? `423 篇` : ""}
        </button>
        <button
          className={tier === "2a" ? "chip active-chip" : "chip"}
          onClick={() => setTier("2a")}
        >
          同源清词 {counts ? `${layerCount("2a").toLocaleString()} 首` : ""}
        </button>
        <button
          className={tier === "2b" ? "chip active-chip" : "chip"}
          onClick={() => setTier("2b")}
        >
          同源宋词 {counts ? `${layerCount("2b").toLocaleString()} 首` : ""}
        </button>
        {counts && (
          <span className="muted">
            同源层覆盖 {counts.poets.toLocaleString()} 位词人、{counts.tunes.toLocaleString()} 个词牌；
            逐句平仄由同源带调拼音统计，<b>非官方口径</b>
          </span>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      <div className="split-2">
        <div>
          <div className="row" style={{ marginBottom: 10 }}>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="在当前层里检索题名、词人、词牌（服务端检索）"
              aria-label="筛选作品"
              style={{ flex: 1, padding: "6px 10px", border: "1px solid var(--rule)", borderRadius: 2, background: "var(--surface)" }}
            />
            <span className="muted">显示 {filtered.length} / {works.length}</span>
          </div>

          {prosody && prosody.available && (
            <details className="panel" style={{ marginBottom: 10 }}>
              <summary style={{ cursor: "pointer" }}>
                <b>词谱体式候选</b>
                <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>
                  {prosody.unique
                    ? "该词牌下只有一个同字数体式"
                    : `${prosody.candidates.length} 个同字数体式候选`}
                  {prosody.allowed_for_alignment ? " · 已给出逐句对照" : " · 多候选，不做逐字对照"}
                </span>
              </summary>
              <div className="kv" style={{ marginTop: 8 }}>
                <span>消歧口径</span>
                <b>总字数硬过滤 + 句读序列 + 分阕位置，取最小错误候选</b>
              </div>
              <table style={{ marginTop: 8 }}>
                <thead>
                  <tr>
                    <th style={{ width: 120 }}>体式</th>
                    <th style={{ width: 70 }}>置信度</th>
                    <th style={{ width: 60 }}>惩罚</th>
                    <th style={{ width: 110 }}>作者</th>
                    <th>句读（每句字数）</th>
                  </tr>
                </thead>
                <tbody>
                  {prosody.candidates.slice(0, 4).map((item) => (
                    <tr key={item.form_id}>
                      <td className="mono">{item.form_id}</td>
                      <td>{item.confidence}</td>
                      <td className="num">{item.penalty}</td>
                      <td className="muted">{item.author ?? "—"}</td>
                      <td className="mono" style={{ fontSize: 10 }}>
                        {JSON.stringify(item.sentences)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="note" style={{ marginTop: 8 }}>{prosody.boundary}</div>
            </details>
          )}

          {doc ? (
            <>
              <div className="row" style={{ marginBottom: 4 }}>
                <b style={{ fontFamily: "var(--font-song)", fontSize: 17 }}>
                  {doc.title}·{doc.tune}
                </b>
                <span className="pill verified">{doc.source_note.includes("官方") ? "逐句：官方标注" : ""}</span>
                <span className="pill">逐字：本地计算</span>
              </div>

              <div className="verses reveal" key={doc.work_id}>
                {doc.lines.map((line, lineIndex) => {
                  const offset = doc.lines
                    .slice(0, lineIndex)
                    .reduce((sum, item) => sum + item.chars.length, 0);
                  return (
                  <div key={line.line_no} id={`line-${line.line_no}`}>
                    <div
                      className={`verse${targetLine === line.line_no ? " hit" : ""}`}
                      role="group"
                      aria-label={`第 ${line.line_no} 句`}
                    >
                      {line.chars.map((char, charIndex) => {
                        const cls =
                          char.ping_ze === "平" ? "ping" : char.ping_ze === "仄" ? "ze" : "unknown";
                        const isSelected =
                          selected?.line === lineIndex && selected?.index === charIndex;
                        return (
                          <span
                            key={`${line.line_no}-${charIndex}`}
                            className={`ch ${cls}${isSelected ? " selected" : ""}`}
                            style={{ animationDelay: `${Math.min((offset + charIndex) * 9, 480)}ms` }}
                            tabIndex={0}
                            role="button"
                            onClick={() => setSelected({ line: lineIndex, index: charIndex })}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                setSelected({ line: lineIndex, index: charIndex });
                              }
                            }}
                          >
                            {char.char}
                            <u>{char.pinyin || "—"}</u>
                          </span>
                        );
                      })}
                    </div>
                    <div className="mono muted" style={{ marginLeft: 4 }}>
                      第 {line.line_no} 句 · {line.character_count} 字 · 平 {line.ping_count} · 仄 {line.ze_count}
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
                    {line.line_no === doc.split_after_line && (
                      <div className="split" aria-hidden="true">
                        <i />
                        <span className="mono">下阕</span>
                        <i />
                      </div>
                    )}
                  </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="panel">正在载入逐字声律…</div>
          )}
        </div>

        <aside>
          <div className="panel">
            <div className="sub" style={{ marginBottom: 8 }}>字音证据</div>
            {activeChar ? (
              <>
                <div style={{ fontFamily: "var(--font-kai)", fontSize: 38, lineHeight: 1, marginBottom: 10 }}>
                  {activeChar.char}
                </div>
                <div className="kv">
                  <span>今音</span>
                  <b>
                    {activeChar.pinyin} · {activeChar.tone_name}
                  </b>
                </div>
                <div className="kv">
                  <span>平仄</span>
                  <b>{activeChar.ping_ze}</b>
                </div>
                <div className="kv">
                  <span>读音来源</span>
                  <b>{activeChar.tier_label}</b>
                </div>
                <div className="kv">
                  <span>候选读音</span>
                  <b className="mono">{activeChar.candidates.join(" / ") || "—"}</b>
                </div>
                {activeChar.unknown_reason && (
                  <div className="note">{activeChar.unknown_reason}</div>
                )}
                <p className="sub" style={{ marginTop: 10, marginBottom: 0 }}>
                  点任意字格查看；四声为本地字典判定，官方只提供逐句平仄计数。
                </p>
              </>
            ) : (
              <p className="muted" style={{ margin: 0 }}>点一个字格，这里显示它的读音与来源。</p>
            )}
          </div>

          <div className="panel">
            <div className="sub" style={{ marginBottom: 8 }}>作品列表</div>
            <div className="list">
              {filtered.map((work) => (
                <button
                  key={work.id}
                  className={work.id === activeId ? "active" : ""}
                  onClick={() => choose(work.id)}
                >
                  <div>{work.title}</div>
                  <div className="muted" style={{ fontSize: 11 }}>
                    {work.poet} · {work.tune} · {work.period}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}

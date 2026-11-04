import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type TuneStats } from "../api";

type SearchItem = {
  id: number | string;
  title: string;
  tune: string;
  period: string;
  poet: string;
  matched: { line_no: number; text: string }[];
  hits: number;
  /** 层级标签：官方标注 / 同源清词（非官方口径）/ 同源宋词（非官方口径） */
  tier_label?: string;
  source?: string;
};

type SearchResult = {
  q: string;
  mode: "trigram" | "bigram" | "unigram" | "substring" | "browse";
  items: SearchItem[];
  total: number;
  index: "sqlite" | "memory";
  tier?: string;
  tier2_total?: number;
  boundary?: string;
};

// 单次最多取多少篇——与后端 /api/search 的 limit 上限一致（500）
const SEARCH_LIMIT_MAX = 500;

const MODE_LABEL: Record<string, string> = {
  trigram: "三字以上 · FTS5 trigram",
  bigram: "两字 · 二元倒排",
  unigram: "单字 · 一元倒排",
  substring: "内存子串（索引未就位）",
  browse: "按条件浏览（无关键词）"
};

function Highlight({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <>{text}</>;
  const parts = text.split(keyword);
  return (
    <>
      {parts.map((part, index) => (
        <span key={index}>
          {part}
          {index < parts.length - 1 && (
            <mark style={{ background: "var(--mark)", color: "var(--cinnabar)", padding: "0 1px" }}>{keyword}</mark>
          )}
        </span>
      ))}
    </>
  );
}

const SAMPLES = ["西风", "梅花", "桂丛香", "临江仙"];

export default function Search() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const preset = (params.get("q") ?? "").trim();
  // 默认展示词：首屏直接给出一批结果，比一张空表更有说服力
  const DEFAULT_QUERY = preset || "西风";
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [tier, setTier] = useState<"1" | "2a" | "2b" | "all">("all");
  const [statsTier, setStatsTier] = useState<"1" | "2a" | "2b">("1");
  const [period, setPeriod] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [tuneName, setTuneName] = useState("");
  const [stats, setStats] = useState<TuneStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [tuneNames, setTuneNames] = useState<string[]>([]);
  const [poet, setPoet] = useState("");
  const [poetQuery, setPoetQuery] = useState("");
  const [poetCandidates, setPoetCandidates] = useState<{ name: string; official_works: number; corpus_works: number }[]>([]);
  const [showPoets, setShowPoets] = useState(false);
  const [limit, setLimit] = useState(50);
  const [filterTune, setFilterTune] = useState("");

  async function loadStats(tune: string, layer: "1" | "2a" | "2b" = "1") {
    const name = tune.trim();
    if (!name) return;
    setStatsError(null);
    try {
      setStats(await api.searchStats(name, layer));
    } catch (err) {
      setStats(null);
      setStatsError(err instanceof Error ? err.message : "同调统计获取失败");
    }
  }

  async function run(
    nextLimit?: number,
    overrides?: { keyword?: string; poet?: string; tune?: string; period?: string }
  ) {
    // overrides 用来解决 React state 的异步问题：
    // 在 onClick 里 setPoet(...) 之后立刻 run()，run 读到的还是**旧值**，
    // 筛选看着生效了（chip 出现了）但请求里没有这个条件。
    // 所以改变筛选的那一下必须把新值直接传进来。
    const useLimit = nextLimit ?? limit;
    const keyword = (overrides?.keyword ?? query).trim();
    const activePoet = overrides?.poet ?? poet;
    const activeTune = overrides?.tune ?? filterTune;
    const activePeriod = overrides?.period ?? period;
    if (!keyword && !(activePoet || activeTune || activePeriod)) {
      setError("给一个检索词，或选作者／词牌／朝代");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const params = new URLSearchParams({ q: keyword, limit: String(useLimit), tier });
      if (activePeriod) params.set("period", activePeriod);
      if (activePoet) params.set("poet", activePoet);
      if (activeTune) params.set("tune", activeTune);
      const response = await fetch("/api/search?" + params.toString());
      const body = await response.json();
      if (!body.ok) throw new Error(body.error?.message ?? "检索失败");
      setResult(body.data as SearchResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检索失败");
    } finally {
      setPending(false);
    }
  }

  useEffect(() => {
    // 首屏直接给出一次结果，比一张空表更有说服力；切换语料层时重跑
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tier]);

  useEffect(() => {
    // 词牌清单用于「按词牌筛选」下拉（后台取，不阻塞首屏）。
    // 官方层 100 个 + 同源层近 2,000 个——同源层的调只在同源层出现，
    // 下拉里没有就等于没法筛。
    api
      .tunes()
      .then((data) => {
        const official = (data.tunes || []).map((item) => item.name).filter(Boolean);
        const corpus = (data.corpus?.names || []).filter(Boolean);
        const merged = Array.from(new Set([...official, ...corpus]));
        setTuneNames(merged);
      })
      .catch(() => setTuneNames([]));
  }, []);

  // 作者候选：输入即查（防抖 250 ms），2,293 位词人装不下拉框
  useEffect(() => {
    if (!showPoets) return;
    let alive = true;
    const timer = setTimeout(async () => {
      try {
        const response = await fetch("/api/authors?limit=12&q=" + encodeURIComponent(poetQuery));
        const body = await response.json();
        if (alive && body.ok) setPoetCandidates(body.data.items);
      } catch {
        if (alive) setPoetCandidates([]);
      }
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [poetQuery, showPoets]);

  return (
    <section>
      <h1>检索</h1>
      <p className="sub">
        支持单字、双字与长串。双字走二元倒排（FTS5 trigram 对两字查询不命中），三字以上走 trigram。
      </p>

      <div className="row">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && run()}
          placeholder="词句、意象或片段"
          aria-label="检索词"
          className="field"
          style={{ flex: 1, minWidth: 220 }}
        />
        <select
          value={period}
          onChange={(event) => {
            const value = event.target.value;
            setPeriod(value);
            setLimit(50);
            void run(undefined, { period: value });
          }}
          aria-label="朝代"
          className="field"
        >
          <option value="">全部朝代</option>
          <option value="清">清</option>
          <option value="宋">宋</option>
          <option value="元">元</option>
        </select>
        <button onClick={() => void run()} disabled={pending}>
          {pending ? "检索中…" : "检索"}
        </button>
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <span className="muted">按作者</span>
        <div className="picker" style={{ flex: "0 1 260px" }}>
          <input
            className="field"
            value={poet ? poet : poetQuery}
            onChange={(event) => {
              setPoet("");
              setPoetQuery(event.target.value);
              setShowPoets(true);
            }}
            onFocus={() => setShowPoets(true)}
            placeholder="词人姓名，如 柳永"
            aria-label="按作者检索"
          />
          {showPoets && (
            <div className="picker-pop">
              <div className="picker-head">
                候选 {poetCandidates.length} 位
                <button className="linkish" onClick={() => setShowPoets(false)}>
                  收起
                </button>
              </div>
              <div className="picker-list">
                {poetCandidates.map((item) => (
                  <button
                    key={item.name}
                    className="picker-item"
                    onClick={() => {
                      setPoet(item.name);
                      setPoetQuery("");
                      setShowPoets(false);
                      setLimit(50);
                      // 选作者 = 看这位词人的作品，关键词让位
                      setQuery("");
                      void run(undefined, { keyword: "", poet: item.name });
                    }}
                  >
                    <span className="pi-title">{item.name}</span>
                    <span className="pi-meta">
                      官方 {item.official_works} 首 · 同源 {item.corpus_works} 首
                    </span>
                  </button>
                ))}
                {poetCandidates.length === 0 && (
                  <div className="muted" style={{ padding: "8px 10px", fontSize: 12 }}>
                    没有匹配的词人。
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        {poet && (
          <span className="chip active-chip">
            作者：{poet}
            <button
              className="linkish"
              style={{ marginLeft: 6 }}
              onClick={() => {
              setPoet("");
              setLimit(50);
              void run(undefined, { poet: "" });
            }}
            >
              ✕
            </button>
          </span>
        )}
        <span className="muted">按词牌</span>
        <select
          className="field"
          value={filterTune}
          onChange={(event) => {
            const value = event.target.value;
            setFilterTune(value);
            setLimit(50);
            // 选词牌 = 看这个调的作品，关键词让位
            setQuery("");
            void run(undefined, { keyword: "", tune: value });
          }}
          aria-label="按词牌筛选"
          style={{ maxWidth: 160 }}
        >
          <option value="">全部词牌</option>
          {tuneNames.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        {(poet || filterTune || period) && (
          <button
            className="linkish"
            onClick={() => {
              setPoet("");
              setFilterTune("");
              setPeriod("");
              setLimit(50);
              // 清空筛选 = 回到初始浏览状态（关键词若已让位则恢复默认展示词）
              const kw = query.trim() ? query : DEFAULT_QUERY;
              setQuery(kw);
              void run(undefined, { keyword: kw, poet: "", tune: "", period: "" });
            }}
          >
            清空筛选
          </button>
        )}
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <button
          className={tier === "all" ? "chip active-chip" : "chip"}
          onClick={() => setTier("all")}
        >
          全部语料
        </button>
        <button
          className={tier === "1" ? "chip active-chip" : "chip"}
          onClick={() => setTier("1")}
        >
          官方标注 423 篇
        </button>
        <button
          className={tier === "2a" ? "chip active-chip" : "chip"}
          onClick={() => setTier("2a")}
        >
          同源清词 26,742 首
        </button>
        <button
          className={statsTier === "2a" ? "chip active-chip" : "chip"}
          onClick={() => {
            setStatsTier("2a");
            if (tuneName) void loadStats(tuneName, "2a");
          }}
          title="同调统计的语料层"
        >
          统计：同源清词
        </button>
        <button
          className={statsTier === "2b" ? "chip active-chip" : "chip"}
          onClick={() => {
            setStatsTier("2b");
            if (tuneName) void loadStats(tuneName, "2b");
          }}
          title="同调统计的语料层"
        >
          统计：同源宋词
        </button>
        <button
          className={tier === "2b" ? "chip active-chip" : "chip"}
          onClick={() => setTier("2b")}
        >
          同源宋词 16,449 首
        </button>
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <span className="muted">试试：</span>
        {SAMPLES.map((item) => (
          <button
            key={item}
            className="chip"
            onClick={() => {
              setQuery(item);
            }}
          >
            {item}
          </button>
        ))}
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      {result && (
        <>
          <div className="row" style={{ marginTop: 12 }}>
            <span className="pill">{MODE_LABEL[result.mode] ?? result.mode}</span>
            {result.mode !== "browse" && (
              <span className="pill">
                {result.index === "sqlite" ? "索引：SQLite" : "索引：内存回退"}
              </span>
            )}
            <span className="muted">
              {result.q ? `关键词「${result.q}」` : "按筛选条件浏览"}
              {poet ? ` · 作者「${poet}」` : ""}
              {filterTune ? ` · 词牌「${filterTune}」` : ""}
              {period ? ` · ${period}` : ""}
              {" · 命中 "}
              <b>{result.total.toLocaleString()}</b> 篇
              {result.items.length < result.total && `，已显示前 ${result.items.length} 篇`}
            </span>
            {result.tier2_total != null && (
              <span className="pill">其中同源层 {result.tier2_total} 篇</span>
            )}
          </div>

          {result.items.length === 0 ? (
            <div className="panel" style={{ marginTop: 10 }}>
              没有命中。可以试更短的词，或去掉朝代筛选。
            </div>
          ) : (
            <>
            <table style={{ marginTop: 12 }}>
              <thead>
                <tr>
                  <th style={{ width: 180 }}>作品</th>
                  <th style={{ width: 130 }}>作者 · 朝代</th>
                  <th style={{ width: 120 }}>层级</th>
                  <th>命中句</th>
                  <th style={{ width: 56 }}>句数</th>
                </tr>
              </thead>
              <tbody>
                {result.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <button className="linkish" onClick={() => navigate(`/work?id=${item.id}`)}>
                        {item.title}
                      </button>
                      <div className="mono muted">
                        <button
                          className="linkish"
                          title="查看该词牌的同调统计"
                          onClick={() => {
                            setTuneName(item.tune);
                            void loadStats(item.tune, statsTier);
                          }}
                        >
                          {item.tune}
                        </button>
                      </div>
                    </td>
                    <td>
                      {item.poet}
                      <span className="muted"> · {item.period}</span>
                    </td>
                    <td>
                      <span
                        className="pill"
                        title={String(item.tier_label ?? "")}
                        style={{ fontSize: 10 }}
                      >
                        {String(item.tier_label ?? item.source ?? "—")}
                      </span>
                    </td>
                    <td>
                      {(item.matched ?? []).map((line) => (
                        <div key={line.line_no} style={{ marginBottom: 2 }}>
                          <span className="mono muted">第 {line.line_no} 句　</span>
                          <Highlight text={line.text} keyword={result.q} />
                        </div>
                      ))}
                      {(item.hits ?? 0) > (item.matched ?? []).length && (
                        <div className="muted">
                          另有 {(item.hits ?? 0) - (item.matched ?? []).length} 句命中
                        </div>
                      )}
                    </td>
                    <td className="num">{item.hits ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {result.items.length < result.total && (
              <div className="row" style={{ marginTop: 10 }}>
                {limit < SEARCH_LIMIT_MAX && (
                  <button
                    onClick={() => {
                      const next = Math.min(limit + 100, SEARCH_LIMIT_MAX);
                      setLimit(next);
                      void run(next);
                    }}
                    disabled={pending}
                  >
                    显示更多（再看 100 篇）
                  </button>
                )}
                <span className="muted">
                  已显示 {result.items.length} / {result.total.toLocaleString()} 篇
                </span>
              </div>
            )}
            </>
          )}
        </>
      )}

      <h2 style={{ marginTop: 26 }}>同调统计</h2>
      <p className="sub">
        按词牌汇总：作品数、词人数、句数分布与仄比中位数。可切换语料层——带官方标注的那层最准。
        句数按「该词牌下出现过的句数 → 篇数」给出，因为同一词牌常有多套句式。
      </p>
      <div className="row">
        <input
          value={tuneName}
          onChange={(event) => setTuneName(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && loadStats(tuneName)}
          placeholder="词牌名，如 临江仙"
          aria-label="词牌名"
          className="field"
          list="tune-names"
          style={{ flex: 1, minWidth: 200 }}
        />
        <datalist id="tune-names">
          {tuneNames.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <button onClick={() => loadStats(tuneName, statsTier)}>统计</button>
      </div>

      {statsError && <div className="error" style={{ marginTop: 10 }}>{statsError}</div>}

      {stats && (
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="row">
            <b>{stats.tune}</b>
            <span className="pill">{stats.tier_label}</span>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <span className="pill">作品 {stats.works} 篇</span>
            <span className="pill">词人 {stats.poets} 位</span>
            <span className="pill">
              仄比中位数 {stats.ze_ratio_median === null ? "—" : `${stats.ze_ratio_median}%`}
            </span>
          </div>

          <div className="row" style={{ marginTop: 12, alignItems: "flex-start", gap: 24 }}>
            <div style={{ flex: "1 1 260px", minWidth: 240 }}>
              <div className="muted" style={{ marginBottom: 4 }}>句数分布</div>
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 90 }}>句数</th>
                    <th style={{ width: 90 }}>篇数</th>
                    <th>占比</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.sentence_counts.map((row) => (
                    <tr key={row.sentences}>
                      <td className="num">{row.sentences}</td>
                      <td className="num">{row.works}</td>
                      <td>
                        <span
                          style={{
                            display: "inline-block",
                            height: 10,
                            width: `${Math.max(2, Math.round((row.works / stats.works) * 100))}%`,
                            background: "var(--accent)",
                            borderRadius: 3,
                            verticalAlign: "middle"
                          }}
                        />
                        <span className="muted" style={{ marginLeft: 8 }}>
                          {Math.round((row.works / stats.works) * 100)}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ flex: "1 1 220px", minWidth: 200 }}>
              <div className="muted" style={{ marginBottom: 4 }}>朝代分布</div>
              <table>
                <thead>
                  <tr>
                    <th>朝代</th>
                    <th style={{ width: 90 }}>篇数</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(stats.periods).map(([period, count]) => (
                    <tr key={period}>
                      <td>{period}</td>
                      <td className="num">{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {stats.boundary && <div className="note" style={{ marginTop: 10 }}>{stats.boundary}</div>}
        </div>
      )}
    </section>
  );
}

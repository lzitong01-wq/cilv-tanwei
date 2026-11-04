import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type TuneOverview, type TuneReview } from "../api";

type Node = { id: string; type: string; label: string; weight: number };
type Edge = { source: string; target: string; relation: string };
type GraphData = {
  nodes: Node[];
  edges: Edge[];
  focus: { type: string; id: string; label: string } | null;
  meta: Record<string, unknown>;
};

const TYPE_LABEL: Record<string, string> = {
  period: "朝代",
  tune: "词牌",
  poet: "词人",
  work: "作品",
  root: "全库"
};

const TYPE_COLOR: Record<string, string> = {
  period: "var(--ok)",
  tune: "var(--cinnabar)",
  poet: "var(--indigo)",
  work: "var(--ink-3)",
  root: "var(--ink)"
};

// 环半径固定，换焦点时位置不跳；朝代永远在最内圈，作品在最外圈只作散点
const RING: Record<string, number> = { period: 92, tune: 158, poet: 224, work: 262 };
const ORDER = ["period", "tune", "poet", "work"];

const W = 780;
const H = 560;
const CX = W / 2;
const CY = 282;
const LABEL_MAX = 8;

// 关系样式：实线=收录/属于，虚线=词牌—词人的使用关系，点线=朝代归属
const RELATION_STYLE: Record<string, { dash?: string; width: number; opacity: number }> = {
  收录: { width: 0.8, opacity: 0.55 },
  属于: { width: 0.8, opacity: 0.5 },
  创作: { width: 0.8, opacity: 0.45 },
  使用: { dash: "3 3", width: 0.8, opacity: 0.6 },
  被使用: { dash: "3 3", width: 0.8, opacity: 0.6 },
  归属: { dash: "1 3", width: 1, opacity: 0.65 },
  分布: { dash: "1 3", width: 1, opacity: 0.65 }
};

function radius(weight: number) {
  return Math.min(13, 3.5 + Math.log2(Math.max(1, weight) + 1) * 1.5);
}

function short(label: string) {
  return label.length > LABEL_MAX ? label.slice(0, LABEL_MAX) + "…" : label;
}

type Point = { node: Node; x: number; y: number; r: number; dx: number; dy: number };

export default function Graph() {
  const [tunes, setTunes] = useState<TuneOverview | null>(null);
  const [review, setReview] = useState<TuneReview | null>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [focusId, setFocusId] = useState<string>("");
  const [hover, setHover] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  // 支持 /graph?view=identity 直达（演示与分享用）
  const [view, setView] = useState<"graph" | "identity">(() =>
    params.get("view") === "identity" ? "identity" : "graph"
  );
  // 焦点可分享：/graph?focus=tune:临江仙 直接打开某节点
  const initialFocus = useRef(params.get("focus") ?? "").current;

  const load = useCallback(
    async (id: string) => {
      setLoading(true);
      try {
        const url = id ? `/api/graph?focus=${encodeURIComponent(id)}` : "/api/graph";
        const response = await fetch(url);
        const body = await response.json();
        if (!body.ok) throw new Error(body.error?.message ?? "加载失败");
        setData(body.data as GraphData);
        setFocusId(id);
        setError(null);
        setParams(id ? { focus: id } : {}, { replace: true });
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载失败");
      } finally {
        setLoading(false);
      }
    },
    [setParams]
  );

  // 只在**首次挂载**时按深链参数加载一次。
  //
  // 不能写成 `useEffect(..., [load, initialFocus])`：`load` 依赖 setParams，
  // 而 setParams 的引用随 URL 变化——于是「点节点 → setParams 写 focus →
  // URL 变 → load 变 → effect 重跑 → load(initialFocus='') → setParams({})
  // → focus 被清空」。后果有两个：URL 里的 focus 永远留不住（**分享链接失效**），
  // 而且每点一次节点都要重新请求一遍概览。
  const loadedOnce = useRef(false);
  useEffect(() => {
    if (loadedOnce.current) return;
    loadedOnce.current = true;
    void load(initialFocus);
  }, [load, initialFocus]);

  useEffect(() => {
    api
      .tunes()
      .then(setTunes)
      .catch(() => undefined);
    api
      .tuneReview()
      .then(setReview)
      .catch(() => undefined);
  }, []);

  const layout = useMemo<Point[]>(() => {
    if (!data) return [];
    const center: Node = data.focus
      ? data.nodes.find((node) => node.id === data.focus!.id) ?? {
          id: "root",
          type: "root",
          label: data.focus.label,
          weight: 1
        }
      : { id: "root", type: "root", label: "全库", weight: data.nodes.length };

    const grouped: Record<string, Node[]> = {};
    for (const node of data.nodes) {
      if (node.id === center.id) continue;
      (grouped[node.type] ?? (grouped[node.type] = [])).push(node);
    }

    const points: Point[] = [{ node: center, x: CX, y: CY, r: radius(center.weight) + 5, dx: 0, dy: 0 }];
    for (const type of ORDER) {
      const nodes = grouped[type] ?? [];
      if (nodes.length === 0) continue;
      const ring = RING[type] ?? 262;
      nodes.forEach((node, index) => {
        // 每圈从正上方开始顺时针均分；单个节点也落在正上方，避免视角乱跳
        const angle = (index / Math.max(1, nodes.length)) * Math.PI * 2 - Math.PI / 2;
        const dx = Math.cos(angle);
        const dy = Math.sin(angle);
        points.push({
          node,
          x: CX + dx * ring,
          y: CY + dy * ring,
          r: radius(node.weight),
          dx,
          dy
        });
      });
    }
    return points;
  }, [data]);

  const position = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    for (const item of layout) map.set(item.node.id, { x: item.x, y: item.y });
    return map;
  }, [layout]);

  const meta = data?.meta ?? {};
  const hovered = layout.find((item) => item.node.id === hover)?.node ?? null;
  const ringsDrawn = ORDER.filter((type) => layout.some((item) => item.node.type === type));

  const go = (node: Node) => {
    if (node.type === "work") {
      navigate(`/work?id=${node.id.split(":", 2)[1]}`);
      return;
    }
    if (node.id === "root") {
      load("");
      return;
    }
    load(node.id);
  };

  return (
    <section>
      <h1>图谱</h1>
      <p className="sub">
        词人—作品—词牌—朝代四类关系。史证接口当前 total=0，史证关系位保留但不虚构填充。
      </p>

      <div className="row" style={{ marginBottom: 10 }}>
        <button className={view === "graph" ? "chip active-chip" : "chip"} onClick={() => setView("graph")}>
          关系图
        </button>
        <button
          className={view === "identity" ? "chip active-chip" : "chip"}
          onClick={() => setView("identity")}
        >
          词调身份层
        </button>
        <span className="muted">同一个词调下可能有多种句式；身份层把它们分开记，不强行定体。</span>
      </div>

      {view === "identity" && <IdentityView tunes={tunes} review={review} />}

      {view === "graph" && (
      <>
      <div className="row">
        <button onClick={() => load("")} disabled={!focusId || loading}>
          回到概览
        </button>
        <span className="pill" style={{ borderColor: data?.focus ? TYPE_COLOR[data.focus.type] : undefined }}>
          {data?.focus ? `${TYPE_LABEL[data.focus.type] ?? ""}：${data.focus.label}` : "全库概览"}
        </span>
        <span className="muted">
          可见节点 {layout.length} · 边 {data?.edges.length ?? 0}
          {typeof meta.works === "number" ? ` · 语料 ${meta.works} 篇` : ""}
        </span>
      </div>

      {error && (
        <div className="error" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}

      <div className="panel" style={{ marginTop: 12, padding: 8, position: "relative", maxWidth: 820, margin: "12px auto 0" }}>
        {loading && (
          <div className="muted mono" style={{ position: "absolute", top: 14, right: 16, fontSize: 11 }}>
            载入中…
          </div>
        )}
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label="词人、作品、词牌与朝代的关系统图"
          style={{ display: "block" }}
        >
          <rect x="0" y="0" width={W} height={H} fill="var(--paper-3)" />

          {/* 词谱格：环线只作参照，不承载数据 */}
          {ringsDrawn.map((type) => (
            <circle
              key={`ring-${type}`}
              cx={CX}
              cy={CY}
              r={RING[type]}
              fill="none"
              stroke="var(--rule)"
              strokeWidth="0.6"
              strokeDasharray="1 5"
              opacity="0.55"
            />
          ))}

          {data?.edges.map((edge, index) => {
            const from = position.get(edge.source);
            const to = position.get(edge.target);
            if (!from || !to) return null;
            const style = RELATION_STYLE[edge.relation] ?? { width: 0.8, opacity: 0.45 };
            const active = hover === edge.source || hover === edge.target;
            return (
              <line
                key={`${edge.source}-${edge.target}-${index}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke={active ? "var(--ink-2)" : "var(--rule)"}
                strokeWidth={active ? 1.3 : style.width}
                strokeDasharray={style.dash}
                opacity={active ? 1 : style.opacity}
              />
            );
          })}

          {layout.map((item) => {
            const color = TYPE_COLOR[item.node.type] ?? "var(--ink-3)";
            const isCenter = item.node.id === (data?.focus?.id ?? "root");
            const isWork = item.node.type === "work";
            // 作品节点只作散点，不常驻标签；其余按径向排列
            const showLabel = !isWork || hover === item.node.id;
            const dim = hover !== null && hover !== item.node.id;
            const anchor = item.dx > 0.35 ? "start" : item.dx < -0.35 ? "end" : "middle";
            const labelX = item.x + item.dx * (item.r + 7);
            const labelY = item.y + item.dy * (item.r + 7) + (anchor === "middle" ? 0 : 3.5);
            const centerR = Math.max(23, item.r + 6);
            // 中心的字要装进圆里：超过 3 字缩号，超过 5 字截断（完整名在悬停行里给）
            const centerLabel =
              item.node.label.length > 5 ? item.node.label.slice(0, 5) + "…" : item.node.label;
            const centerFont = item.node.label.length > 3 ? 9 : 12;
            return (
              <g
                key={item.node.id}
                role="button"
                tabIndex={0}
                aria-label={`${TYPE_LABEL[item.node.type] ?? ""} ${item.node.label}`}
                style={{ cursor: "pointer" }}
                onMouseEnter={() => setHover(item.node.id)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(item.node.id)}
                onBlur={() => setHover(null)}
                onClick={() => go(item.node)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    go(item.node);
                  }
                }}
              >
                <circle cx={item.x} cy={item.y} r={Math.max(item.r + 6, 12)} fill="transparent" />
                <circle
                  cx={item.x}
                  cy={item.y}
                  r={isWork ? 3.4 : isCenter ? centerR : item.r}
                  fill={isCenter ? color : "var(--paper-3)"}
                  stroke={color}
                  strokeWidth={isCenter ? 0 : isWork ? 1 : 1.7}
                  opacity={dim ? 0.4 : isWork ? 0.9 : 1}
                />
                {isCenter && (
                  <circle
                    cx={item.x}
                    cy={item.y}
                    r={centerR + 5}
                    fill="none"
                    stroke={color}
                    strokeWidth="0.8"
                    opacity="0.4"
                  />
                )}
                {showLabel && (
                  <text
                    x={isCenter ? item.x : labelX}
                    y={isCenter ? item.y : labelY}
                    textAnchor={isCenter ? "middle" : anchor}
                    dominantBaseline={isCenter ? "central" : anchor === "middle" ? "middle" : "auto"}
                    fontSize={isCenter ? centerFont : isWork ? 9 : 11.5}
                    fontFamily="var(--font-song)"
                    fill={isCenter ? "var(--paper-3)" : "var(--ink-2)"}
                    letterSpacing={isCenter ? "0.5" : undefined}
                    style={{ pointerEvents: "none" }}
                  >
                    {isCenter ? centerLabel : short(item.node.label)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>

      <div className="row" style={{ marginTop: 8, minHeight: 24 }}>
        {hovered ? (
          <span className="muted">
            {TYPE_LABEL[hovered.type] ?? ""} · <b style={{ color: "var(--ink)" }}>{hovered.label}</b>
            {hovered.type === "work" ? " · 点击进入单篇解析" : ` · 关联 ${hovered.weight} 篇`}
          </span>
        ) : (
          <span className="muted">点词人／词牌／朝代继续下钻，点作品进入单篇解析</span>
        )}
      </div>

      <div className="row" style={{ marginTop: 4 }}>
        {Object.entries(TYPE_LABEL).map(([type, label]) => (
          <span key={type} className="pill" style={{ borderColor: TYPE_COLOR[type], color: TYPE_COLOR[type] }}>
            {label}
          </span>
        ))}
        <span className="muted mono" style={{ fontSize: 11 }}>
          实线 收录／属于 · 虚线 使用 · 点线 归属
        </span>
      </div>

      <div className="note">
        图谱只覆盖官方 423 篇的元数据关系。史证层（历史事件、词人生平、作品关联）接口当前为空，
        因此这一层显示"待发布"，不用推测内容填充。
      </div>
      </>
      )}
    </section>
  );
}


function IdentityView({ tunes, review }: { tunes: TuneOverview | null; review: TuneReview | null }) {
  const [keyword, setKeyword] = useState("");
  const [layer, setLayer] = useState<"official" | "corpus">("official");
  const rows = useMemo(() => {
    const items = layer === "corpus" ? tunes?.corpus?.top ?? [] : tunes?.tunes ?? [];
    const key = keyword.trim();
    if (!key) return items.slice(0, 40);
    return items.filter((item) => item.name.includes(key) || item.poets.some((poet) => poet.includes(key)));
  }, [tunes, keyword, layer]);

  if (!tunes) {
    return <div className="panel muted">正在载入词调身份层…</div>;
  }

  const stats = tunes.stats;
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <button
          className={layer === "official" ? "chip active-chip" : "chip"}
          onClick={() => setLayer("official")}
        >
          官方层（{stats.tunes} 词调）
        </button>
        <button
          className={layer === "corpus" ? "chip active-chip" : "chip"}
          onClick={() => setLayer("corpus")}
        >
          同源层（{tunes.corpus?.tunes ?? 0} 词调）
        </button>
        <span className="muted">
          {layer === "official"
            ? "官方标注派生的体式，带分阕，是最准确的一层。"
            : "同源 4.3 万首派生的体式，只按字数序列判定（非官方口径），不含分阕。"}
        </span>
      </div>

      <div className="grid">
        <div className="stat"><span>词调</span><strong>{stats.tunes}</strong></div>
        <div className="stat"><span>体式</span><strong>{stats.forms}</strong></div>
        <div className="stat"><span>多体式词调</span><strong>{stats.multi_form_tunes}</strong></div>
        <div className="stat">
          <span>唯一命中词调率</span>
          <strong style={{ fontSize: 18 }}>{(stats.unique_tune_rate * 100).toFixed(1)}%</strong>
        </div>
      </div>

      {tunes.corpus?.available && (
        <div className="note" style={{ marginTop: 10 }}>
          同源层：<b>{tunes.corpus.works.toLocaleString()}</b> 首 ·{" "}
          <b>{tunes.corpus.tunes.toLocaleString()}</b> 个词调 ·{" "}
          <b>{tunes.corpus.forms.toLocaleString()}</b> 种体式（分阕未知，按字数序列）。
          {tunes.corpus.boundary}
        </div>
      )}

      <div className="note" style={{ marginTop: 8 }}>{tunes.boundary}</div>

      <div className="row" style={{ marginTop: 12 }}>
        <input
          className="field"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="按词牌或词人过滤"
          aria-label="过滤词调"
          style={{ maxWidth: 280 }}
        />
        <span className="muted">显示 {rows.length} 条（最多 40）</span>
      </div>

      <table style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th style={{ width: 150 }}>词调</th>
            <th style={{ width: 90 }}>作品数</th>
            <th style={{ width: 90 }}>体式数</th>
            <th style={{ width: 130 }}>朝代分布</th>
            <th>体式（句式字数序列）</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.tune_id}>
              <td>
                <div style={{ fontFamily: "var(--font-song)" }}>{item.name}</div>
                <div className="muted mono" style={{ fontSize: 10 }}>{item.tune_id}</div>
              </td>
              <td className="num">{item.works}</td>
              <td className="num">{item.forms.length}</td>
              <td className="mono" style={{ fontSize: 10 }}>
                {Object.entries(item.periods).map(([period, count]) => `${period}${count}`).join(" / ")}
              </td>
              <td>
                {item.forms.map((form) => (
                  <div key={form.form_id} className="mono" style={{ fontSize: 10 }}>
                    {form.display}　[{JSON.stringify(form.pattern)}]　{form.work_ids.length} 篇
                    <span className="muted">　业务键 {form.business_key}</span>
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {review && (
        <>
          <h2>别名归并抽样复核</h2>
          <div className="row">
            <span className="pill">抽样 {review.size} 条</span>
            <span className="pill verified">已归并 {review.merged}</span>
            <span className={`pill ${review.unmerged.length ? "unverified" : "verified"}`}>
              待人工复核 {review.unmerged.length}
            </span>
            <span className="muted mono">种子 {review.seed}</span>
          </div>
          <div className="note" style={{ marginTop: 8 }}>{review.note}</div>
          {review.unmerged.length > 0 && (
            <div className="panel" style={{ marginTop: 8 }}>
              <div className="sub" style={{ marginBottom: 6 }}>待复核的别名（不强行归并）</div>
              {review.unmerged.map((item) => (
                <div key={item.alias} className="mono" style={{ fontSize: 11 }}>
                  「{item.alias}」→ 词调 {item.tune}：{item.rule}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

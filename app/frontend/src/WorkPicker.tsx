import { useEffect, useMemo, useState } from "react";
import { api, type WorkSummary } from "./api";

type Tier = "all" | "1" | "2a" | "2b" | "2c" | "2d";

// 计数**不写死**：语料扩容过一次（43,614 → 54,292），写死数字立刻过时。
// 各层作品数统一从 /api/meta 取，新增层级只需在后端 meta 里多给一行。
const TIERS: { key: Tier; label: string; hint: string }[] = [
  { key: "all", label: "全部", hint: "五层合并检索，官方标注优先" },
  { key: "1", label: "官方标注", hint: "带官方逐句声律标注，唯一基准口径" },
  { key: "2a", label: "同源清词", hint: "poetry-source 清词，逐句平仄按带调拼音统计，非官方口径" },
  { key: "2b", label: "同源宋词", hint: "poetry-source 宋词，逐句平仄按带调拼音统计，非官方口径" },
  { key: "2c", label: "同源历代词", hint: "明/元/唐/五代/金/隋/南北朝，逐句平仄按带调拼音统计，非官方口径" },
  { key: "2d", label: "公开整理本·今音", hint: "chinese-poetry《全宋词》去重新增部分，无拼音、声律由本地今音计算，非官方口径" }
];

type Props = {
  value: string;
  onChange: (id: string) => void;
  label?: string;
};

/**
 * 作品选择器：分层 + 搜索。
 *
 * 为什么不用 `<select>`：语料有 4.3 万首，下拉框装不下，也不便按词人找。
 * 这里默认列官方标注那一层（口径最准），切层或输入关键词即向服务端检索。
 */
export default function WorkPicker({ value, onChange, label = "选择作品" }: Props) {
  const [tier, setTier] = useState<Tier>("all");
  const [keyword, setKeyword] = useState("");
  const [items, setItems] = useState<WorkSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [tierCounts, setTierCounts] = useState<Record<string, number>>({});
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api.meta().then((meta) => {
      const counts: Record<string, number> = {};
      for (const item of meta.tiers ?? []) {
        const key = item.tier === "Tier 1" ? "1" : item.tier.replace("Tier ", "");
        counts[key] = item.works;
      }
      counts.all = meta.corpus_total ?? (meta.works ?? 0) + (meta.tier2?.works ?? 0);
      setTierCounts(counts);
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    let alive = true;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.works({
          tier,
          q: keyword.trim() || undefined,
          page_size: 20
        });
        if (!alive) return;
        setItems(data.items);
        setTotal(data.total);
      } catch {
        if (alive) setItems([]);
      } finally {
        if (alive) setLoading(false);
      }
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [tier, keyword]);

  const current = useMemo(
    () => items.find((item) => String(item.id) === value),
    [items, value]
  );

  return (
    <div className="picker">
      <div className="row" style={{ marginBottom: 6 }}>
        <span className="muted" style={{ minWidth: 56 }}>{label}</span>
        {TIERS.map((item) => (
          <button
            key={item.key}
            className={tier === item.key ? "chip active-chip" : "chip"}
            title={item.hint}
            onClick={() => setTier(item.key)}
          >
            {item.label}
          </button>
        ))}
        <span className="muted" style={{ fontSize: 11 }}>
          {tierCounts[tier] != null ? `${tierCounts[tier].toLocaleString()} 首` : ""}
        </span>
      </div>

      <input
        className="field"
        value={keyword}
        onChange={(event) => setKeyword(event.target.value)}
        onFocus={() => setOpen(true)}
        placeholder="搜题名、词人或词牌（全库）…"
        aria-label="搜索作品"
      />

      {open && (
        <div className="picker-pop">
          <div className="picker-head">
            {loading ? "检索中…" : `命中 ${total.toLocaleString()} 篇`}
            <button className="linkish" onClick={() => setOpen(false)}>
              收起
            </button>
          </div>
          <div className="picker-list">
            {items.map((item) => (
              <button
                key={String(item.id)}
                className={String(item.id) === value ? "picker-item on" : "picker-item"}
                onClick={() => {
                  onChange(String(item.id));
                  setOpen(false);
                }}
              >
                <span className="pi-title">{item.title || "无题"}</span>
                <span className="pi-meta">
                  {item.poet} · {item.tune || "—"} · {item.period}
                </span>
                <span className="pi-tier">{item.tier_label}</span>
              </button>
            ))}
            {!loading && items.length === 0 && (
              <div className="muted" style={{ padding: "8px 10px", fontSize: 12 }}>
                这一层没有匹配的作品，换个词或换一层。
              </div>
            )}
          </div>
        </div>
      )}

      {current && (
        <div className="muted" style={{ marginTop: 4, fontSize: 11 }}>
          已选：《{current.title}》 {current.poet} ｜ {current.tier_label}
        </div>
      )}
    </div>
  );
}

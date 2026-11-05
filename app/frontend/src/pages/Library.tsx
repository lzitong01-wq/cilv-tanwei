import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import BookCover from "../BookCover";
import { api, type TuneOverview } from "../api";

type Layer = "1" | "2a" | "2b" | "2c" | "2d" | "all";

const LAYERS: { key: Layer; label: string; hint: string }[] = [
  { key: "1", label: "官方标注", hint: "带官方标注的 423 首，唯一基准口径" },
  { key: "2a", label: "同源清词", hint: "poetry-source 清词，带调拼音，非官方口径" },
  { key: "2b", label: "同源宋词", hint: "poetry-source 宋词，带调拼音，非官方口径" },
  { key: "2c", label: "同源历代词", hint: "明/元/唐/五代十国/金/隋/南北朝，带调拼音，非官方口径" },
  { key: "2d", label: "整理本·今音", hint: "chinese-poetry《全宋词》去重新增部分，声律为本地今音计算，非官方口径" },
  { key: "all", label: "全部", hint: "五层合并浏览" }
];

const SORTS = [
  { key: "poet", label: "按词人排序" },
  { key: "title", label: "按书名排序" },
  { key: "tune", label: "按词牌排序" }
];

/**
 * 书库：竖版书影封面网格。
 *
 * 这是参照古籍书库（jingdian.ancientbooks.cn）的主页面形态做的——
 * 顶部工具条 + 封面网格。封面图形全部现画（CSS + SVG），不引用他站图片。
 */
export default function Library() {
  const navigate = useNavigate();
  const [layer, setLayer] = useState<Layer>("2a");
  const [sort, setSort] = useState("poet");
  const [keyword, setKeyword] = useState("");
  const [tune, setTune] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<{ id: string | number; title: string; tune: string; poet: string; period: string }[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [tunes, setTunes] = useState<TuneOverview | null>(null);
  const PAGE_SIZE = 40;

  useEffect(() => {
    api.tunes().then(setTunes).catch(() => undefined);
  }, []);

  useEffect(() => {
    let alive = true;
    setPending(true);
    const params = new URLSearchParams({
      tier: layer,
      page: String(page),
      page_size: String(PAGE_SIZE)
    });
    if (keyword.trim()) params.set("q", keyword.trim());
    if (tune) params.set("tune", tune);
    fetch("/api/works?" + params.toString())
      .then((response) => response.json())
      .then((body) => {
        if (!alive) return;
        if (!body.ok) throw new Error(body.error?.message ?? "取书失败");
        setItems(body.data.items);
        setTotal(body.data.total);
        setError(null);
      })
      .catch((err: Error) => alive && setError(err.message))
      .finally(() => alive && setPending(false));
    return () => {
      alive = false;
    };
  }, [layer, keyword, tune, page]);

  const ordered = useMemo(() => {
    const copy = [...items];
    if (sort === "title") copy.sort((a, b) => a.title.localeCompare(b.title, "zh"));
    else if (sort === "tune") copy.sort((a, b) => a.tune.localeCompare(b.tune, "zh"));
    else copy.sort((a, b) => a.poet.localeCompare(b.poet, "zh") || a.title.localeCompare(b.title, "zh"));
    return copy;
  }, [items, sort]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section>
      <h1>书库</h1>
      <p className="sub">
        按书影浏览全部语料。官方标注 {tunes?.stats.works ?? 423} 首是唯一基准口径；
        同源层是「读词」与「风格」的底子，标着非官方口径。
      </p>

      <div className="library-bar">
        <div className="seg">
          {LAYERS.map((item) => (
            <button
              key={item.key}
              className={layer === item.key ? "on" : ""}
              title={item.hint}
              onClick={() => {
                setLayer(item.key);
                setPage(1);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <input
          className="field"
          value={keyword}
          onChange={(event) => {
            setKeyword(event.target.value);
            setPage(1);
          }}
          placeholder="搜书名、词人"
          aria-label="搜书名、词人"
          style={{ width: 190 }}
        />
        <select
          className="field"
          value={tune}
          onChange={(event) => {
            setTune(event.target.value);
            setPage(1);
          }}
          aria-label="按词牌筛选"
          style={{ width: 140 }}
        >
          <option value="">全部词牌</option>
          {(tunes?.corpus?.all ?? []).map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}（{item.works}）
            </option>
          ))}
        </select>
        <select
          className="field"
          value={sort}
          onChange={(event) => setSort(event.target.value)}
          aria-label="排序"
          style={{ width: 130 }}
        >
          {SORTS.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </select>
        <div className="spacer" />
        <span className="muted">
          {pending ? "取书中…" : `共 ${total.toLocaleString()} 首 · 第 ${page} / ${pages} 页`}
        </span>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="library-grid">
        {ordered.map((item) => (
          <BookCover
            key={String(item.id)}
            seed={String(item.id)}
            title={item.title}
            tune={item.tune || "无题"}
            sub={item.poet ? `${item.poet} · ${item.period}` : item.period}
            mark={item.period}
            onClick={() => navigate(`/work?id=${encodeURIComponent(String(item.id))}`)}
          />
        ))}
      </div>

      {!pending && ordered.length === 0 && (
        <div className="panel" style={{ marginTop: 14 }}>
          这一层里没有匹配的书。换个词或换一层看看。
        </div>
      )}

      <div className="row" style={{ marginTop: 22 }}>
        <button onClick={() => setPage(1)} disabled={page <= 1}>
          首页
        </button>
        <button onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page <= 1}>
          上一页
        </button>
        <span className="muted">
          第 {page} / {pages} 页
        </span>
        <button onClick={() => setPage((value) => Math.min(pages, value + 1))} disabled={page >= pages}>
          下一页
        </button>
        <button onClick={() => setPage(pages)} disabled={page >= pages}>
          末页
        </button>
      </div>
    </section>
  );
}

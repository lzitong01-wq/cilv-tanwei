import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, type Health } from "./api";
import Home from "./pages/Home";
import Solve from "./pages/Solve";
import Work from "./pages/Work";
import Search from "./pages/Search";
import EvalCenter from "./pages/EvalCenter";
import Qa from "./pages/Qa";
import Style from "./pages/Style";
import Revisions from "./pages/Revisions";
import Exports from "./pages/Exports";
import Argument from "./pages/Argument";
import Parse from "./pages/Parse";
import Graph from "./pages/Graph";
import Invariants from "./pages/Invariants";
import Sentiment from "./pages/Sentiment";
import Brief from "./pages/Brief";
import Compare from "./pages/Compare";
import Library from "./pages/Library";
import About from "./pages/About";
import StatusBanner from "./StatusBanner";
import ThemeToggle from "./ThemeToggle";
import GlobalSearch from "./GlobalSearch";
import HelpPanel from "./HelpPanel";
import ErrorBoundary from "./ErrorBoundary";

const GROUPS: { title: string; items: { to: string; label: string }[] }[] = [
  {
    title: "智能体",
    items: [{ to: "/qa", label: "问词（提问）" }]
  },
  {
    // 赛题要求的「标准化验证集测试」——上传验证集、逐题调用内部接口作答
    title: "验证集测试",
    items: [{ to: "/eval", label: "上传与跑分" }]
  },
  {
    title: "读词",
    items: [
      { to: "/library", label: "书库" },
      { to: "/work", label: "单篇解析" },
      { to: "/parse", label: "解析新词" },
      { to: "/style", label: "风格对比" },
      { to: "/compare", label: "逐句对照" }
    ]
  },
  {
    title: "求证",
    items: [
      { to: "/argument", label: "论证辅助" },
      { to: "/sentiment", label: "情感候选" },
      { to: "/solve", label: "题面求解" },
      { to: "/search", label: "检索" }
    ]
  },
  { title: "校订", items: [{ to: "/revisions", label: "差异校订" }, { to: "/exports", label: "导出记录" }] },
  { title: "导航", items: [{ to: "/graph", label: "图谱" }] },
  { title: "说明", items: [{ to: "/about", label: "语料与授权" }] },
  // 下面这组是给评审与维护者看的材料，日常使用不必进；视觉上弱化
  {
    title: "项目材料",
    items: [
      { to: "/brief", label: "答辩准备" },
      { to: "/invariants", label: "不变量与用例" }
    ]
  }
];

/** 顶栏主导航（与左侧目录树互补：这里只放主要去处） */
const TOP_NAV = [
  { to: "/", label: "概览" },
  { to: "/library", label: "书库" },
  { to: "/qa", label: "问词" },
  { to: "/work", label: "单篇解析" },
  { to: "/style", label: "风格对比" },
  { to: "/search", label: "检索" }
];

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [failed, setFailed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  // 标签页标题跟着页面走：多标签对照时能分清哪一页
  useEffect(() => {
    const hit = GROUPS.flatMap((group) => group.items).find((item) =>
      item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to)
    );
    document.title = hit && hit.to !== "/" ? `${hit.label} · 词律探微` : "词律探微 · 清代词律声情研究助手";
  }, [location.pathname]);

  useEffect(() => {
    let alive = true;
    api
      .health()
      .then((data) => alive && setHealth(data))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const state = failed ? "bad" : health?.db === "ok" ? "ok" : "";
  const label = failed
    ? "未连接"
    : health
      ? `语料 ${(health.corpus_total ?? health.works).toLocaleString()} 首`
      : "检查中";

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          词律探微<small>清代词律声情研究助手</small>
        </div>
        <nav className="topnav">
          {TOP_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "on" : "")}
              end={item.to === "/"}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        <button className="ask-top" onClick={() => navigate("/qa")} title="向词律探微提问">
          问一句
        </button>
        <GlobalSearch />
        <HelpPanel />
        <ThemeToggle />
        <span className="tag">
          <i className={`dot ${state}`} />
          {label}
        </span>
      </header>
      <StatusBanner health={health} failed={failed} />
      <div className="body">
        <nav className="side">
          {GROUPS.map((group) => (
            <div key={group.title} className={group.title === "项目材料" ? "material" : undefined}>
              <h4>{group.title}</h4>
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"}>
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
<main className="content">
            {/* 错误边界包在内容区而不是整个应用：某一页渲染异常时，
                顶栏与左侧导航仍然可用，用户能直接切到别的页或回概览。 */}
            <ErrorBoundary>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/library" element={<Library />} />
                <Route path="/work" element={<Work />} />
                <Route path="/solve" element={<Solve />} />
                <Route path="/search" element={<Search />} />
                <Route path="/style" element={<Style />} />
                <Route path="/compare" element={<Compare />} />
                <Route path="/qa" element={<Qa />} />
                <Route path="/revisions" element={<Revisions />} />
                <Route path="/exports" element={<Exports />} />
                <Route path="/parse" element={<Parse />} />
                <Route path="/argument" element={<Argument />} />
                <Route path="/sentiment" element={<Sentiment />} />
                <Route path="/brief" element={<Brief />} />
                <Route path="/graph" element={<Graph />} />
                <Route path="/invariants" element={<Invariants />} />
                <Route path="/about" element={<About />} />
                <Route path="/eval" element={<EvalCenter />} />
                <Route
                  path="*"
                  element={
                    <div className="panel">
                      页面不存在。<NavLink to="/">回到概览</NavLink>
                    </div>
                  }
                />
              </Routes>
            </ErrorBoundary>
          </main>
      </div>
    </div>
  );
}

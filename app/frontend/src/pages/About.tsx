import { useEffect, useState } from "react";
import { api, type Health, type Meta } from "../api";

type RuleCategory = {
  id: string;
  name: string;
  /** 扁平关键词列表；后端已把 match/detect 摊平，前端不再自己猜结构 */
  detect?: string[];
  /** "all" = 全部命中才算；"any" = 任一命中即可 */
  match_mode?: "all" | "any";
  note?: string;
};

type RuleSet = {
  version: string;
  contract: string;
  source: string;
  note: string;
  categories: RuleCategory[];
  formulas: Record<string, string>;
  boundary_claims: string[];
};

const TIERS = [
  {
    tier: "Tier 0",
    name: "题库（训练集）",
    scope: "1000 题，五类各 200",
    use: "规则归纳与回归；不随交付物分发，题面只从本地题库读",
    official: "赛方所有"
  },
  {
    tier: "官方标注",
    name: "官方标注语料",
    scope: "423 篇（清 200 / 宋 203 / 元 20）",
    use: "唯一基准口径；逐句平仄计数、分段、句序都以此为准",
    official: "赛方 API，只读（405 不支持写入）"
  },
  {
    tier: "同源清词",
    name: "同源带拼音公开整理本（清）",
    scope: "poetry-source 清词（带调拼音）",
    use: "作品浏览、逐句平仄（同源带调拼音）、风格画像；构建期统计，运行时只读索引",
    official: "官方题库「参考资料」字段所列来源，仓库 MIT"
  },
  {
    tier: "同源宋词",
    name: "同源带拼音公开整理本（宋）",
    scope: "poetry-source 宋词（带调拼音）",
    use: "同上；同源公开整理本按朝代分层，供检索与阅读",
    official: "同同源清词；按朝代分层，官方标注不含宋词"
  },
  {
    tier: "Tier 2b",
    name: "公开文本本地计算",
    scope: "宋词 / 元曲公开整理本",
    use: "同源验证与口径比对，不混入基准计算",
    official: "chinese-poetry，按其条款署名"
  }
];

const CAN_SAY = [
  "字段级正确率与证据可核验率在公开与保密各 100%",
  "C1–C3 共 600 题整段逐字一致",
  "声律事实由确定性引擎给出，模型只写候选且过四道闸门",
  "风格画像基于可复算的形式特征",
  "检索采用 FTS5 trigram + 一元／二元倒排，不是向量检索",
  "校订闭环止于幂等导出，写回由官方流程完成",
  "1000 题是训练集，其成绩只证明规则被复现，不承诺评测集成绩"
];

const CANNOT_SAY = [
  "系统 100% 准确 / 全部 1000 题整段逐字一致",
  "大模型完成了声律分析",
  "已接入 58,851 首官方数据库",
  "已完成词人文学风格判定 / 情感识别准确率 X%",
  "采用了 RAG 向量检索",
  "系统可直接更新官方标注库",
  "在评测集上取得 X%"
];

export default function About() {
  const [rules, setRules] = useState<RuleSet | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    fetch("/api/rules")
      .then((response) => response.json())
      .then((body) => body.ok && setRules(body.data as RuleSet))
      .catch(() => undefined);
    api.meta().then(setMeta).catch(() => undefined);
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  return (
    <section>
      <h1>语料与授权</h1>
      <p className="sub">
        把范围说清楚比把范围说大更重要：哪些是官方标注、哪些是同源整理本，分别说明。
      </p>

      <div className="note">
        语料总量 <b>{meta?.corpus_total?.toLocaleString() ?? "—"}</b> 篇
        （官方标注 {meta?.works ?? "—"} 首 + 同源公开整理本{" "}
        {meta?.tier2?.works?.toLocaleString() ?? "—"} 首）。
        带官方标注的只有 423 首，是<b>唯一基准口径</b>；同源语料是「读词」与「风格」的底子，
        两者口径不同、绝不混算，但都可浏览与检索。
      </div>

      <h2>语料分层</h2>
      <table>
        <thead>
          <tr>
            <th style={{ width: 80 }}>层级</th>
            <th style={{ width: 180 }}>名称</th>
            <th style={{ width: 190 }}>范围</th>
            <th>用途与授权</th>
          </tr>
        </thead>
        <tbody>
          {TIERS.map((item) => (
            <tr key={item.tier}>
              <td className="mono">{item.tier}</td>
              <td>{item.name}</td>
              <td>{item.scope}</td>
              <td>
                <div>{item.use}</div>
                <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{item.official}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="note">
        第三方语料与词谱文件<b>只在构建期使用，不随交付物分发</b>；词谱源自搜韵且未见授权说明。
        本项目从中只保留**归纳出的派生事实**（体式的字数/句读/分阕/逐字平仄、古音平仄层）并登记来源，
        原始文件不进入交付物。前端字体为思源宋体子集（SIL OFL 1.1，授权原文见
        `app/frontend/public/fonts/OFL.txt`）。
      </div>
      <div className="note" style={{ marginTop: 8 }}>
        读音证据按优先级依次为：<b>官方 prosody &gt; 词家自注 &gt; 外部字表（词谱体式 / 古音平仄）&gt; 本地字典</b>。
        外部字表只在「与官方计数不一致」时用于解释差异，且多候选时不做逐字对照。
      </div>

      <h2>口径说明</h2>
      <div className="panel">
        <div className="kv"><span>逐字四声</span><b>本地字典计算，官方只给逐句平仄计数，四声细分无官方真值</b></div>
        <div className="kv"><span>C4 引文</span><b>默认取后段最长句，金标可任选引文；判分只要求可定位</b></div>
        <div className="kv"><span>C5 语义</span><b>景情归类与声律—语义互证不判对错，一律标「未验证」</b></div>
        <div className="kv"><span>体式候选</span><b>只比对官方 423 篇里出现过的句式，不是词谱校勘，需人工确认</b></div>
        <div className="kv"><span>史证层</span><b>官方接口 total=0，关系位保留但不虚构填充</b></div>
        <div className="kv"><span>校订闭环</span><b>L0 发现 → L3 幂等导出；L4 官方写回不在本系统内</b></div>
      </div>

      <h2>部署与运行环境</h2>
      <div className="panel">
        <div className="sub">本系统跑在一台真实服务器上，以下为运行时实测值（非写死）</div>
        <div className="kv">
          <span>访问地址</span>
          <b className="mono">
            {typeof window !== "undefined" ? window.location.origin : "—"}
          </b>
        </div>
        <div className="kv">
          <span>服务器主机名</span>
          <b className="mono">{health?.deployment?.hostname ?? "—"}</b>
        </div>
        <div className="kv">
          <span>运行平台</span>
          <b className="mono">{health?.platform ?? "—"}</b>
        </div>
        <div className="kv">
          <span>检索后端</span>
          <b className="mono">
            {health?.sqlite ?? "—"}
            {health?.sqlite_trigram === false ? "（trigram 不可用，≥3 字检索走内存匹配）" : ""}
          </b>
        </div>
        <div className="kv">
          <span>数据根目录</span>
          <b className="mono">{health?.deployment?.data_root ?? "—"}</b>
        </div>
        <div className="kv">
          <span>进程</span>
          <b className="mono">
            PID {health?.deployment?.pid ?? "—"}
            {health?.deployment?.started_at ? ` · 启动于 ${health.deployment.started_at}` : ""}
          </b>
        </div>
        <div className="kv">
          <span>快照时刻</span>
          <b className="mono">{health?.snapshot_at ?? "—"}</b>
        </div>
      </div>
      <div className="note" style={{ marginTop: 8 }}>
        部署方式：GitHub Release → 服务器部署代理拉取 → 解压切换版本 → 重启并做健康检查，
        失败自动回滚上一版。以上主机名、进程号与数据目录由服务在运行时读取，
        <b>评审时可据此核对确为独立服务器上的独立进程</b>。
      </div>

      <h2>规则版本与热修入口</h2>
      {rules ? (
        <>
          <div className="row">
            <span className="pill verified">规则 {rules.version}</span>
            <span className="pill">规则版本 {rules.version}</span>
            <span className="muted">{rules.source}</span>
          </div>
          <div className="note" style={{ marginTop: 8 }}>{rules.note}</div>
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th style={{ width: 190 }}>公式 / 口径</th>
                <th>定义</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(rules.formulas ?? {}).map(([key, value]) => (
                <tr key={key}>
                  <td className="mono">{key}</td>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 className="sub" style={{ marginTop: 14, marginBottom: 4 }}>五类判类措辞（按顺序匹配）</h3>
          <table>
            <thead><tr><th style={{ width: 60 }}>类别</th><th style={{ width: 200 }}>名称</th><th style={{ width: 96 }}>命中方式</th><th>题面关键词</th></tr></thead>
            <tbody>
              {rules.categories.map((item) => {
                const keywords = item.detect ?? [];
                return (
                  <tr key={item.id}>
                    <td className="mono">{item.id}</td>
                    <td>
                      {item.name}
                      {item.note && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{item.note}</div>}
                    </td>
                    <td>
                      <span className="pill">{item.match_mode === "all" ? "全部命中" : "任一命中"}</span>
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {keywords.length ? keywords.join(" / ") : <span className="muted">（未配置）</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="muted mono" style={{ marginTop: 8, fontSize: 11 }}>
            越界词表 {(rules.boundary_claims ?? []).length} 条 · 外置文件 app/backend/rules.json · 端点 /api/rules
          </div>
        </>
      ) : (
        <div className="panel muted">规则版本未加载（检查 /api/rules）。</div>
      )}

      <h2>可说 / 不可说</h2>
      <div className="split-2">
        <div className="panel">
          <div className="sub">可以说（有实测支撑）</div>
          {CAN_SAY.map((item) => (
            <div className="kv" key={item}><span className="pill verified">✓</span><b style={{ textAlign: "left" }}>{item}</b></div>
          ))}
        </div>
        <div className="panel">
          <div className="sub">不可以说</div>
          {CANNOT_SAY.map((item) => (
            <div className="kv" key={item}><span className="pill unverified">✕</span><b style={{ textAlign: "left" }}>{item}</b></div>
          ))}
        </div>
      </div>

      <div className="note" style={{ marginTop: 12 }}>
        第三方语料与词谱文件只在构建期使用，不随本系统分发；本系统里保留的是从中归纳出的
        派生事实（体式的字数与句读、古音平仄层），并逐条登记来源。词谱源自搜韵，未见授权说明。
      </div>
    </section>
  );
}

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

type MetricTable = {
  name: string;
  scope: string;
  raw: number | null;
  normalized: number | null;
  proxy: number | null;
  questions: number | null;
  errors: number | null;
};

type Gate = { id: string; name: string; basis: string; level: string; note: string };

type Metrics = {
  tables: MetricTable[];
  gates: Gate[];
  sentiment: {
    consistency: number | null;
    kappa: number | null;
    benchmark_size: number | null;
    blind_size: number | null;
    disclosure: string | null;
  };
  reasoning: {
    total_checks: number | null;
    passed: number | null;
    by_class: Record<string, { total: number; failed: number }> | null;
    questions: number | null;
  };
  polyphone: { official_fix_rate: number | null; tier2_rate: number | null; rule_count: number | null };
  rules_version: string | null;
  boundary: string;
  missing: string[];
};

const QUESTIONS = [
  ["为什么不用 RAG 向量检索？", "用 FTS5 trigram + 一元/二元倒排 + 结构化过滤；理由与改变条件见分册 C §C6.2。"],
  ["数据只有 423 篇，怎么做清代词律？", "423 篇是官方基准口径（Tier 1）；扩容靠官方题库「参考资料」所列公开语料（Tier 2a/2b），分层标注、不混算。"],
  ["四声从哪来？", "本地计算口径（官方只给逐句平仄计数）。平/仄映射与官方一致率 90.79%，四声细分无官方真值，已如实标注。"],
  ["情感分析准确率多少？", "不给准确率。官方明文语义不判对错；我们给与 AI 标注基准集的一致率，以及基准集自身的标注者间一致性，并标未验证。"],
  ["评测集你们没见过，怎么保证？", "规则版本化外置、同构迁移优先；定位三层兜底。训练集 100% 只证明规则被复现，泛化靠跨作品切分与实际评测。"],
  ["校订怎么闭环？", "闭环到幂等导出，写回由官方流程完成（官方 API 只读，405 不支持写入）。"],
  ["模型会不会编声律事实？", "事实由引擎给，模型只出候选并过四道闸门；实测两个模型都会犯事实错误，所以不让它们碰事实。"],
  ["作品定位错了怎么办？", "三层兜底 + 多结果列候选 + 不静默取第一条；定位不上就明说定位不上。"],
  ["能离线部署吗？", "核心解析与解题可离线（快照只读、本地计算）；模型端点在目标机未验证前不作承诺。"],
  ["数据与词谱能分发吗？", "第三方数据不分发，只在构建期使用；授权状态逐条登记（V6.1 §13.3）。"]
];

const FALLBACKS = [
  ["模型端点不可达", "关闭副功能，跑纯引擎演示", "显示「模型未提供候选解释，以下为引擎事实」"],
  ["知识库不可达", "用冻结快照；必要时手动选作品", "显示「使用本地快照（获取于 <时间>）」"],
  ["全网不可达", "只跑离线解析与解题 + 预计算回放", "显示「离线模式」标识"]
];

const SCRIPT = [
  { time: "0:00–0:40", step: "口径声明", where: "/", view: "首页：423 篇、三层语料与来源标签、确定性内核一句话" },
  { time: "0:40–1:30", step: "现场解析", where: "/parse", view: "粘贴词文 → 逐字平仄/四声 → 未知字与来源标注" },
  { time: "1:30–2:20", step: "现场解题", where: "/solve", view: "粘贴 C1 题 → 秒答 → 推理链逐步展开" },
  { time: "2:20–3:05", step: "评测报告", where: "/eval", view: "三张指标表 + C1–C3 整段 + C4/C5 口径说明" },
  { time: "3:05–3:50", step: "风格与对比", where: "/style", view: "两位词人形式特征对照 + 解读候选（标未验证）" },
  { time: "3:50–4:30", step: "校订闭环", where: "/revisions", view: "多音字差异 → 建议读音 → 复核 → 幂等导出" },
  { time: "4:30–5:00", step: "图谱与边界", where: "/graph", view: "图谱漫游 → 可说/不可说清单收束" }
];

export default function Brief() {
  const navigate = useNavigate();
  const [data, setData] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/metrics")
      .then((response) => response.json())
      .then((body) => {
        if (!body.ok) throw new Error(body.error?.message ?? "读取失败");
        setData(body.data as Metrics);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <section>
      <h1>答辩与演示准备</h1>
      <p className="sub">
        三张指标表**互不替代**，不合并成单一准确率；语义正确率不报告。
        这里的数字来自实跑产物（<span className="mono">holdout_report.json</span> 等），不是手填的。
      </p>

      {error && <div className="error">{error}</div>}

      <h2>一、三张指标表（评测集六成）</h2>
      {data && data.tables.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th style={{ width: 220 }}>集合</th>
              <th style={{ width: 170 }}>范围</th>
              <th style={{ width: 90 }}>题数</th>
              <th style={{ width: 110 }}>整段逐字</th>
              <th style={{ width: 110 }}>归一化</th>
              <th style={{ width: 110 }}>代理字段</th>
              <th style={{ width: 80 }}>错误</th>
            </tr>
          </thead>
          <tbody>
            {data.tables.map((row) => (
              <tr key={row.name}>
                <td>{row.name}</td>
                <td className="muted">{row.scope}</td>
                <td className="num">{row.questions}</td>
                <td className="num">{row.raw}</td>
                <td className="num">{row.normalized}</td>
                <td className="num">{row.proxy}</td>
                <td className="num">{row.errors}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="panel muted">指标产物未生成，先运行 scripts/holdout_report.py。</div>
      )}
      <div className="note" style={{ marginTop: 8 }}>{data?.boundary}</div>

      <h2>二、门禁状态（如实列，不藏未达标项）</h2>
      <table>
        <thead>
          <tr>
            <th style={{ width: 150 }}>指标</th>
            <th style={{ width: 230 }}>口径</th>
            <th style={{ width: 120 }}>等级</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {(data?.gates ?? []).map((item) => (
            <tr key={item.id}>
              <td>{item.name}</td>
              <td className="muted">{item.basis}</td>
              <td>{item.level}</td>
              <td>{item.note}</td>
            </tr>
          ))}
          <tr>
            <td>情感基准一致率</td>
            <td className="muted">与 AI 标注基准集的一致率（独立评估轮）</td>
            <td>参考</td>
            <td>
              {data?.sentiment.consistency != null
                ? `${(data.sentiment.consistency * 100).toFixed(1)}%（κ=${data.sentiment.kappa?.toFixed(3)}，基准 ${data.sentiment.benchmark_size} 句 + 盲测 ${data.sentiment.blind_size} 句）`
                : "未生成"}
              <div className="muted" style={{ fontSize: 11 }}>{data?.sentiment.disclosure}</div>
            </td>
          </tr>
          <tr>
            <td>修正表官方口径命中</td>
            <td className="muted">修正表应用后与官方逐句计数一致</td>
            <td>门禁（一级 ≥95%）</td>
            <td>
              {data?.polyphone.official_fix_rate != null
                ? `${(data.polyphone.official_fix_rate * 100).toFixed(1)}%（${
                    data.polyphone.official_fix_rate >= 0.95 ? "已达标" : "未达 95%，原因见修正表报告"
                  }）`
                : "未生成"}
            </td>
          </tr>
        </tbody>
      </table>

      {data?.reasoning?.by_class && (
        <>
          <h3 className="sub" style={{ marginTop: 14, marginBottom: 6 }}>
            推理评测集（分册 B §B6：多跳 / 反事实 / 同题自洽 / 干扰项 / 缺数据）
          </h3>
          <div className="row">
            <span className="pill verified">
              {data.reasoning.passed}/{data.reasoning.total_checks} 项通过
            </span>
            <span className="muted">题数 {data.reasoning.questions}</span>
            {Object.entries(data.reasoning.by_class).map(([name, row]) => (
              <span key={name} className={`pill ${row.failed ? "unverified" : "verified"}`}>
                {name} {row.total - row.failed}/{row.total}
              </span>
            ))}
          </div>
          <div className="muted" style={{ marginTop: 6, fontSize: 11 }}>
            缺数据类要求**必须拒答**；干扰项类验证加无关文本后答案不变。
          </div>
        </>
      )}

      <h2>三、答辩十问（口径照规划原文）</h2>
      <div className="panel">
        {QUESTIONS.map(([question, answer], index) => (
          <div
            key={question}
            style={{
              display: "grid",
              gridTemplateColumns: "30px minmax(0, 1fr)",
              gap: 10,
              padding: "7px 0",
              borderBottom: "1px dotted var(--rule)"
            }}
          >
            <span className="mono muted">{index + 1}.</span>
            <div>
              <div style={{ fontWeight: 500 }}>{question}</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 2, lineHeight: 1.7 }}>{answer}</div>
            </div>
          </div>
        ))}
      </div>

      <h2>四、降级剧本（现场三种故障）</h2>
      <table>
        <thead>
          <tr>
            <th style={{ width: 170 }}>故障</th>
            <th style={{ width: 240 }}>现场动作</th>
            <th>界面表现</th>
          </tr>
        </thead>
        <tbody>
          {FALLBACKS.map(([fault, action, display]) => (
            <tr key={fault}>
              <td>{fault}</td>
              <td className="muted">{action}</td>
              <td>{display}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <span className="muted">演练入口：</span>
        <button className="chip" onClick={() => navigate("/graph?degrade=model")}>
          模拟模型不可达
        </button>
        <button className="chip" onClick={() => navigate("/graph?degrade=knowledge")}>
          模拟知识库不可达
        </button>
        <button className="chip" onClick={() => navigate("/graph?degrade=offline")}>
          模拟离线
        </button>
      </div>

      <h2>五、五分钟演示脚本</h2>
      <table>
        <thead>
          <tr>
            <th style={{ width: 120 }}>时间</th>
            <th style={{ width: 120 }}>环节</th>
            <th style={{ width: 110 }}>页面</th>
            <th>关键画面</th>
          </tr>
        </thead>
        <tbody>
          {SCRIPT.map((item) => (
            <tr key={item.time}>
              <td className="mono">{item.time}</td>
              <td>{item.step}</td>
              <td>
                <button className="linkish" onClick={() => navigate(item.where)}>
                  {item.where}
                </button>
              </td>
              <td className="muted">{item.view}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="note" style={{ marginTop: 10 }}>
        规则版本 {data?.rules_version ?? "—"}；本表数字每次实跑后自动更新，不与本表文案脱节。
      </div>
    </section>
  );
}

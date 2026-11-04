import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

const SECTIONS: { title: string; body: string }[] = [
  {
    title: "读词 · 看声律",
    body: "书库按书影浏览全部语料；单篇解析给逐句平仄与逐字读音；解析新词处理不在库内的词句；风格对比看词人的形式偏好；逐句对照把两篇并排看。"
  },
  {
    title: "求证 · 找证据",
    body: "问词先给引擎算出的事实，再给模型写的候选解读；论证辅助把证据卡拼成论证段；情感候选给九类标签；题面求解按官方规则复算题面；检索按单字／双字／长串分流。"
  },
  {
    title: "校订 · 改差异",
    body: "差异校订列出本地计算与官方标注不一致的句；接受或驳回后可导出 CSV；导出记录保留每次导出的幂等键。"
  },
  {
    title: "项目材料",
    body: "给评审与维护者看的四页：评测中心（跑分与逐题下钻）、答辩准备（指标表、问答与演示脚本）、不变量与用例、语料与授权。日常使用不需要进这几页。"
  }
];

/** 智能体路由：引擎优先，答不上来才调模型 */
const AGENT_ROUTING = [
  "① 锚点解析：解析不出 → 明确报错（模型也不知道怎么定位作品）",
  "② 作品定位：本地快照 → 官方 API → 同源层；定位不到 → 明确报错",
  "③ 引擎判类 + 确定性求解：成功 → 出可复算的字段，另可附模型解读",
  "④ 引擎答不上来（题型超出规则覆盖）→ 模型依据事实卡兜底作答，过四道闸门"
];

const AGENT_DISCIPLINE = [
  "数字永远来自引擎，模型不产出判分字段",
  "模型兜底必须有事实卡——没有依据宁可报错，不让模型编数字",
  "来源逐条标注：确定性求解 / 模型解读 / 模型兜底（未验证）"
];

const BOUNDARIES = [
  ["官方标注", "423 首带官方标注的词，逐句平仄计数以此为准。"],
  ["本地计算", "逐字读音由本地字典与多音字修正表推出，与官方不一致时以官方为准，差异进差异校订。"],
  ["待复核", "模型生成的情感标签与自由文本解析均为非官方口径，界面上标注「未验证」，需人工确认后使用。"]
];

/** 顶栏帮助：把「这个系统能做什么、边界在哪」放在随手可及的位置。 */
export default function HelpPanel() {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="help" ref={boxRef}>
      <button
        className="theme-toggle"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        帮助
      </button>
      {open && (
        <div className="help-panel" role="dialog" aria-label="使用帮助">
          <h3>怎么用</h3>
          {SECTIONS.map((item) => (
            <div key={item.title} className="help-block">
              <b>{item.title}</b>
              <p>{item.body}</p>
            </div>
          ))}

          <h3>智能体怎么作答</h3>
          {AGENT_ROUTING.map((item) => (
            <div key={item} className="muted" style={{ fontSize: 12, lineHeight: 1.7 }}>
              {item}
            </div>
          ))}
          <div style={{ marginTop: 8 }}>
            {AGENT_DISCIPLINE.map((item) => (
              <div key={item} className="muted" style={{ fontSize: 12, lineHeight: 1.7 }}>
                · {item}
              </div>
            ))}
          </div>

          <h3>口径边界</h3>
          <table className="help-table">
            <tbody>
              {BOUNDARIES.map(([label, body]) => (
                <tr key={label}>
                  <th style={{ width: 84 }}>{label}</th>
                  <td>{body}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>快捷键</h3>
          <ul className="help-list">
            <li>
              <span className="mono">/</span> 聚焦顶栏检索框
            </li>
            <li>
              <span className="mono">Enter</span> 执行当前页的检索或求解
            </li>
            <li>
              <span className="mono">Esc</span> 关闭本面板
            </li>
          </ul>

          <div className="row" style={{ marginTop: 12 }}>
            <Link to="/about" onClick={() => setOpen(false)}>
              <button>语料与授权</button>
            </Link>
            <Link to="/brief" onClick={() => setOpen(false)}>
              <button>项目材料</button>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { api, type WorkSummary } from "../api";

type Line = {
  line_no: number;
  text: string;
  character_count: number;
  ping_count: number;
  ze_count: number;
};

type LineDoc = { work_id: number; split_after_line: number; lines: Line[] };

/**
 * 逐句平仄对照（分册 B §B7「逐句平仄对照（两篇/两位并排）」）。
 * 数据来自官方逐句计数，并排列出句数、字数与平仄构成的差异。
 */
export default function Compare() {
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [leftId, setLeftId] = useState("58633");
  const [rightId, setRightId] = useState("50955");
  const [left, setLeft] = useState<LineDoc | null>(null);
  const [right, setRight] = useState<LineDoc | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .works({ page: 1, page_size: 100 })
      .then((data) => setWorks(data.items))
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    setError(null);
    Promise.all([api.lines(Number(leftId)), api.lines(Number(rightId))])
      .then(([a, b]) => {
        setLeft(a);
        setRight(b);
      })
      .catch((err: Error) => setError(err.message));
  }, [leftId, rightId]);

  const rows = useMemo(() => {
    const count = Math.max(left?.lines.length ?? 0, right?.lines.length ?? 0);
    const out = [];
    for (let index = 0; index < count; index += 1) {
      const a = left?.lines[index];
      const b = right?.lines[index];
      const sameLength = a && b && a.character_count === b.character_count;
      const sameTone = a && b && a.ping_count === b.ping_count && a.ze_count === b.ze_count;
      out.push({ a, b, sameLength, sameTone });
    }
    return out;
  }, [left, right]);

  const diffCount = rows.filter((row) => !(row.sameLength && row.sameTone)).length;

  return (
    <section>
      <h1>逐句平仄对照</h1>
      <p className="sub">
        两篇并排看句数、字数与平仄构成。数据为官方逐句计数，
        可直接对照句读与分阕的异同。
      </p>

      <div className="row">
        <select
          className="field"
          value={leftId}
          onChange={(event) => setLeftId(event.target.value)}
          aria-label="甲篇"
          style={{ maxWidth: 280 }}
        >
          {works.map((work) => (
            <option key={work.id} value={work.id}>
              甲：{work.title}·{work.tune}｜{work.poet}
            </option>
          ))}
        </select>
        <select
          className="field"
          value={rightId}
          onChange={(event) => setRightId(event.target.value)}
          aria-label="乙篇"
          style={{ maxWidth: 280 }}
        >
          {works.map((work) => (
            <option key={work.id} value={work.id}>
              乙：{work.title}·{work.tune}｜{work.poet}
            </option>
          ))}
        </select>
        <span className="muted">
          甲 {left?.lines.length ?? 0} 句（分阕 {left?.split_after_line ?? "—"}） ·
          乙 {right?.lines.length ?? 0} 句（分阕 {right?.split_after_line ?? "—"}） ·
          逐句有差异 {diffCount} 处
        </span>
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      <table style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th style={{ width: 52 }}>句序</th>
            <th>甲篇</th>
            <th style={{ width: 150 }}>甲 平/仄 · 字数</th>
            <th>乙篇</th>
            <th style={{ width: 150 }}>乙 平/仄 · 字数</th>
            <th style={{ width: 96 }}>差异</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td className="num">{row.a?.line_no ?? row.b?.line_no ?? index + 1}</td>
              <td style={{ fontFamily: "var(--font-song)" }}>{row.a?.text ?? "—"}</td>
              <td className="mono" style={{ fontSize: 11 }}>
                {row.a ? `平${row.a.ping_count} 仄${row.a.ze_count} · ${row.a.character_count}字` : "—"}
              </td>
              <td style={{ fontFamily: "var(--font-song)" }}>{row.b?.text ?? "—"}</td>
              <td className="mono" style={{ fontSize: 11 }}>
                {row.b ? `平${row.b.ping_count} 仄${row.b.ze_count} · ${row.b.character_count}字` : "—"}
              </td>
              <td>
                {row.sameLength && row.sameTone ? (
                  <span className="pill verified">同</span>
                ) : (
                  <span className="pill unverified">
                    {row.sameLength ? "平仄异" : "字数异"}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="note" style={{ marginTop: 12 }}>
        逐句计数为官方标注；「同」指该句字数与平仄构成完全一致，「字数异／平仄异」只描述差异事实，
        不推断原因。句数或字数差异是体式不同（调名 ≠ 体式）的常见表现。
      </div>
    </section>
  );
}

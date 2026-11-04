/**
 * 竖版书影封面。
 *
 * 参照古籍书库（jingdian.ancientbooks.cn）的书影封面观感——**2:3 竖版**、
 * 竖排词牌名、右下朱印、纸面磨损。图形全部是 CSS + SVG 现画的，
 * 没有引用他站图片（版权风险），配色走 token，昼夜两套自动切换。
 */

export type CoverTone = 1 | 2 | 3 | 4 | 5;

const TONE_VAR: Record<CoverTone, string> = {
  1: "var(--cover-1a)",
  2: "var(--cover-2a)",
  3: "var(--cover-3a)",
  4: "var(--cover-4a)",
  5: "var(--cover-5a)"
};

const TONE_VAR_B: Record<CoverTone, string> = {
  1: "var(--cover-1b)",
  2: "var(--cover-2b)",
  3: "var(--cover-3b)",
  4: "var(--cover-4b)",
  5: "var(--cover-5b)"
};

const TONE_VAR_INK: Record<CoverTone, string> = {
  1: "var(--cover-1ink)",
  2: "var(--cover-2ink)",
  3: "var(--cover-3ink)",
  4: "var(--cover-4ink)",
  5: "var(--cover-5ink)"
};

/** 按 id 稳定取一个色调：同一首词每次渲染都是同一张封面。 */
export function toneOf(seed: string): CoverTone {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  }
  return ((hash % 5) + 1) as CoverTone;
}

type Props = {
  title: string;
  tune: string;
  /** 封面下方书名下的副题（作者·朝代） */
  sub?: string;
  /** 封面竖排小字（朝代标记） */
  mark?: string;
  seed: string;
  onClick?: () => void;
  selected?: boolean;
};

/** 词牌名过长时截断——整句当调名时（源数据的噪声）不能让它撑满封面。 */
function trimTune(tune: string): string {
  const clean = (tune || "").trim();
  return clean.length > 6 ? clean.slice(0, 6) + "…" : clean;
}

export default function BookCover({ title, tune, sub, mark, seed, onClick, selected }: Props) {
  const tone = toneOf(seed);
  const shown = trimTune(tune);
  const long = shown.length > 4;
  return (
    <button
      className={"book" + (selected ? " selected" : "")}
      onClick={onClick}
      title={`${title}（${tune}）`}
    >
      <span
        className="cover"
        style={{
          background: `linear-gradient(155deg, ${TONE_VAR[tone]} 0%, ${TONE_VAR_B[tone]} 100%)`,
          color: TONE_VAR_INK[tone]
        }}
      >
        <span className={"vtitle" + (long ? " long" : "")}>{shown || "无题"}</span>
        {mark && (
          <span className="cover-foot">
            <span className="cover-rule" />
            <span className="vsub">{mark}</span>
          </span>
        )}
        <span className="seal" aria-hidden="true">
          詞
        </span>
      </span>
      <span className="book-meta">
        <span className="book-title">{title}</span>
        {sub && <span className="book-sub">{sub}</span>}
      </span>
    </button>
  );
}

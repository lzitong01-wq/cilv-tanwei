import { useEffect, useState } from "react";

/**
 * 四态配色：跟随系统 / 浅黄（宣纸）/ 白 / 夜间。
 * 首次绘制前由 index.html 的内联脚本先定一次，避免深色用户看到白屏闪一下；
 * 这里只负责后续切换与"跟随系统"时的实时响应。
 */
type Mode = "auto" | "light" | "plain" | "dark";

const ORDER: Mode[] = ["auto", "light", "plain", "dark"];
const LABEL: Record<Mode, string> = { auto: "自动", light: "浅黄", plain: "白", dark: "夜" };
const GLYPH: Record<Mode, string> = { auto: "◐", light: "○", plain: "▢", dark: "●" };
const KEY = "cilv-theme";

function readMode(): Mode {
  // 演示与截图可以用 ?theme=dark 直接指定，优先级高于本地保存
  const forced = /[?&]theme=(dark|light|plain|auto)/.exec(window.location.href);
  if (forced) return forced[1] as Mode;
  const saved = localStorage.getItem(KEY);
  return saved === "light" || saved === "plain" || saved === "dark" || saved === "auto"
    ? (saved as Mode)
    : "auto";
}

export default function ThemeToggle() {
  const [mode, setMode] = useState<Mode>(readMode);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      if (mode === "auto") {
        document.documentElement.dataset.theme = media.matches ? "dark" : "light";
      } else {
        document.documentElement.dataset.theme = mode;
      }
    };
    apply();
    if (mode !== "auto") return;
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [mode]);

  const next = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];

  return (
    <button
      className="tag theme-toggle"
      onClick={() => {
        localStorage.setItem(KEY, next);
        setMode(next);
      }}
      title={`配色：${LABEL[mode]}（点击切到${LABEL[next]}）`}
      aria-label={`配色：${LABEL[mode]}，点击切换到${LABEL[next]}`}
    >
      <span aria-hidden="true">{GLYPH[mode]}</span> {LABEL[mode]}
    </button>
  );
}

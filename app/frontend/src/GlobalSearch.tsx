import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

/** 顶栏全局检索：输入词句后回车，直接落到检索页。 */
export default function GlobalSearch() {
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);
      // "/" 聚焦搜索框；在输入框里不抢按键
      if (event.key === "/" && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const keyword = value.trim();
    if (!keyword) return;
    navigate(`/search?q=${encodeURIComponent(keyword)}`);
  }

  return (
    <form className="global-search" onSubmit={submit} role="search">
      <input
        ref={inputRef}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="全局检索词句…"
        aria-label="全局检索"
        title="按 / 聚焦；回车检索"
      />
      <button type="submit" aria-label="检索">
        检索
      </button>
    </form>
  );
}

import { useState } from "react";
import type { Health } from "./api";

/**
 * 降级横幅。
 * 模型挂、知识库不可达、全网不可达时只降级副功能，核心解析与解题照常可用；
 * 界面必须说清发生了什么，而不是整页失败或悄悄不给结果。
 *
 * 文案面向使用者，不出现内部层级代号（快照 / 契约 / Tier 1）。
 */
export default function StatusBanner({ health, failed }: { health: Health | null; failed: boolean }) {
  const [dismissed, setDismissed] = useState(false);

  // 演示彩排：?degrade=model|knowledge|offline 可现场切到对应故障态
  const forced = new URLSearchParams(window.location.search).get("degrade");
  const offline = failed || forced === "offline";
  const modelDown = forced === "model" || (health?.model === "unreachable" && forced !== "knowledge");
  const snapshotOnly = forced === "knowledge" || health?.knowledge === "offline_snapshot";

  let title = "";
  let detail = "";
  if (offline) {
    title = "离线模式";
    detail = "未能连接服务。页面已加载的内容可继续查看；核心解析与解题需要恢复服务后重跑。";
  } else if (modelDown) {
    title = "模型未提供候选解释";
    detail = "以下为引擎算出的事实。解析新词、题面求解、检索、差异校订、论证辅助与风格对比不依赖模型，照常可用。";
  } else if (snapshotOnly && health?.snapshot_at) {
    title = "正在使用本地语料副本";
    detail =
      `语料副本更新于 ${health.snapshot_at}。本系统不联网抓取数据，` +
      `其中 ${health.works} 首带官方标注，其余为同源公开整理本（逐句平仄按带调拼音统计，非官方口径）。`;
  }

  if (!title || dismissed) return null;

  return (
    <div className="banner" role="status" aria-live="polite">
      <span className="pill unverified">{title}</span>
      <span>{detail}</span>
      <div className="spacer" />
      <button
        className="chip"
        onClick={() => setDismissed(true)}
        aria-label="关闭降级提示"
      >
        知道了
      </button>
    </div>
  );
}

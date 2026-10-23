import { t } from "./i18n";
export const statusNames: Record<string, string> = {
  active: "可用",
  available: "可调度",
  busy: "占用中",
  cooling: "冷却中",
  cooldown: "冷却中",
  invalid: "已失效",
  disabled: "已停用",
  queued: "排队中",
  pending: "等待中",
  running: "运行中",
  succeeded: "已完成",
  succeeded_after_retry: "重投后成功",
  completed: "已完成",
  ok: "成功",
  failed: "失败",
  cancelled: "已取消",
  ready: "待入池",
  imported: "已导入",
  duplicate: "重复",
  error: "错误",
  retry_text_only: "只回文本重投",
  attachment_requested: "追问附件",
  media_link_detected: "链接兜底提取",
  media_missing: "只回文本缺失",
  timeout: "生成超时",
  interrupted: "重启中断",
};
export function badge(value: string) {
  const isBad = [
    "invalid",
    "failed",
    "error",
    "media_missing",
    "timeout",
    "interrupted",
  ].includes(value);
  const isGood = [
    "active",
    "available",
    "succeeded",
    "succeeded_after_retry",
    "completed",
    "ok",
    "imported",
    "ready",
    "media_link_detected",
  ].includes(value);
  return (
    <span className={`badge ${isBad ? "bad" : isGood ? "good" : ""}`}>
      {t(statusNames[value] ?? value)}
    </span>
  );
}

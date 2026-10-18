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
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  ready: "待入池",
  imported: "已导入",
  duplicate: "重复",
  error: "错误",
};
export function badge(value: string) {
  return (
    <span
      className={`badge ${["invalid", "failed", "error"].includes(value) ? "bad" : ["active", "available", "succeeded", "completed", "imported", "ready"].includes(value) ? "good" : ""}`}
    >
      {statusNames[value] ?? value}
    </span>
  );
}

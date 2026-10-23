// Turns Engine.status() into short readable text. Never renders cookie values or tokens:
// only fields that Engine.status() already exposes are read here.
const DEVICE: Record<string, string> = {
  not_paired: "未配对",
  paired: "已配对",
  paired_sync_disabled: "已配对，未开启同步",
  paused: "同步已暂停",
  device_expired: "设备授权已过期，请重新配对",
  device_unauthorized: "设备授权已撤销，请重新配对",
};
const SOURCE: Record<string, string> = {
  awaiting_login: "等待登录 Muse",
  ambiguous: "检测到多组会话，已跳过",
  collected: "已采集，等待上传",
  committed: "已同步",
  retry_wait: "上传失败，稍后重试",
  retry_exhausted: "多次重试失败，已放弃本次候选",
  rejected: "服务器拒绝",
  needs_identity_confirmation: "等待管理员确认账号身份",
  conflict: "版本冲突，等待管理员确认",
  confirmation_required: "等待管理员确认",
  candidate_lost_new_capture_requires_confirmation:
    "候选已丢失，需重新采集并确认",
};
interface SourceView {
  source?: string;
  state?: string;
  capture_id?: string;
  account_id?: string;
  credential_version?: number;
}
export interface StatusView {
  paired?: boolean;
  origin?: string;
  enabled?: boolean;
  state?: string;
  sources?: SourceView[];
  blocked?: boolean;
}
export function label(map: Record<string, string>, code?: string): string {
  if (!code) return "—";
  return map[code] ?? code;
}
function storeName(source?: string): string {
  try {
    const [store, partition] = JSON.parse(source ?? "") as [
      string,
      { topLevelSite?: string } | null,
    ];
    const base = store === "0" ? "默认窗口" : `Cookie 存储 ${store}`;
    return partition?.topLevelSite
      ? `${base}（分区 ${partition.topLevelSite}）`
      : base;
  } catch {
    return "未知来源";
  }
}
export function describeStatus(status: StatusView | undefined): string {
  if (!status) return "无法读取状态";
  // Engine keeps the pairing-time state after sync is enabled; do not show a stale "disabled".
  const device = !status.paired
    ? "未配对"
    : status.enabled &&
        (status.state === "paired_sync_disabled" || status.state === "paused")
      ? "已配对"
      : label(DEVICE, status.state);
  const lines = [`设备：${device}`];
  if (status.origin) lines.push(`服务器：${status.origin}`);
  lines.push(`自动同步：${status.enabled ? "开启" : "关闭"}`);
  const sources = status.sources ?? [];
  if (!sources.length) lines.push("会话：暂无");
  for (const s of sources) {
    lines.push(`${storeName(s.source)}：${label(SOURCE, s.state)}`);
    if (s.capture_id) lines.push(`  候选 ${s.capture_id}`);
    if (s.account_id)
      lines.push(
        `  账号 ${s.account_id}` +
          (s.credential_version === undefined
            ? ""
            : ` · 版本 ${s.credential_version}`),
      );
  }
  if (status.blocked) lines.push("有候选等待管理员处理。");
  return lines.join("\n");
}
export function headline(status: StatusView | undefined): {
  text: string;
  tone: "ok" | "wait" | "warn" | "off";
} {
  if (!status?.paired) return { text: "未配对", tone: "off" };
  if (
    status.state === "device_expired" ||
    status.state === "device_unauthorized"
  )
    return { text: label(DEVICE, status.state), tone: "warn" };
  if (!status.enabled) return { text: "同步未开启", tone: "off" };
  if (status.blocked) return { text: "等待管理员确认", tone: "warn" };
  const states = (status.sources ?? []).map((s) => s.state);
  if (
    states.some(
      (s) => s === "retry_wait" || s === "rejected" || s === "retry_exhausted",
    )
  )
    return { text: "上传遇到问题", tone: "warn" };
  if (states.includes("committed")) return { text: "同步正常", tone: "ok" };
  if (states.includes("collected")) return { text: "正在同步", tone: "wait" };
  return { text: "等待登录 Muse", tone: "wait" };
}

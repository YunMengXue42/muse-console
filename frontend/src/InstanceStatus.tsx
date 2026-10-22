import { useEffect, useRef, useState } from "react";
import type { Status } from "./types";
import "./instance-status.css";

export type InstanceTone =
  "online" | "warn" | "offline" | "unreachable" | "pending";

const LABEL: Record<InstanceTone, string> = {
  online: "在线",
  warn: "无可用账号",
  offline: "驱动异常",
  unreachable: "连接断开",
  pending: "连接中",
};

const HINT: Record<InstanceTone, string> = {
  online: "驱动正常",
  warn: "新请求会排队",
  offline: "请求会失败",
  unreachable: "管理接口无响应，自动重试中",
  pending: "等待服务响应",
};

export function instanceTone(
  status: Status | undefined,
  unreachable: boolean,
): InstanceTone {
  if (unreachable) return "unreachable";
  if (!status) return "pending";
  if (!status.driver.ok) return "offline";
  const total = status.accounts.total ?? 0;
  const available = status.accounts.available ?? 0;
  if (total > 0 && available === 0) return "warn";
  return "online";
}

function ago(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 5) return "刚刚同步";
  if (s < 60) return `${s} 秒前同步`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} 分钟前同步` : `${Math.floor(m / 60)} 小时前同步`;
}

/** 侧栏实例状态：一行状态词 + 可用账号数 + 细进度条；细节放悬停提示。 */
export function InstanceStatus({
  status,
  unreachable,
  lastSyncAt,
}: {
  status: Status | undefined;
  unreachable: boolean;
  lastSyncAt: number | null;
}) {
  const tone = instanceTone(status, unreachable);
  const [now, setNow] = useState(() => Date.now());
  const [flash, setFlash] = useState(false);
  const previous = useRef(tone);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    if (previous.current === tone) return;
    previous.current = tone;
    setFlash(true);
    const id = setTimeout(() => setFlash(false), 1200);
    return () => clearTimeout(id);
  }, [tone]);

  const total = status?.accounts.total ?? 0;
  const available = status?.accounts.available ?? 0;
  const showCount = !!status && !unreachable && total > 0;
  const ratio = total > 0 ? Math.min(1, available / total) : 0;
  const details = [
    HINT[tone],
    status && !unreachable
      ? `${status.driver.driver} · v${status.version}`
      : null,
    lastSyncAt != null ? ago(now - lastSyncAt) : null,
  ]
    .filter(Boolean)
    .join("\n");
  const dot =
    tone === "online"
      ? "online"
      : tone === "warn"
        ? "warn"
        : tone === "pending"
          ? "pending"
          : "down";

  return (
    <div
      className={`instance-card tone-${tone}${flash ? " is-flash" : ""}`}
      data-tone={tone}
      title={details}
    >
      <div className="instance-status" role="status">
        <span className="instance-signal" aria-hidden="true">
          <span aria-hidden="true" className={`dot ${dot}`} />
        </span>
        <span className="instance-state">{LABEL[tone]}</span>
        {showCount && (
          <span className="instance-count">
            <strong>{available}</strong>/{total}
          </span>
        )}
        <span className="sr-only">{details}</span>
      </div>
      {showCount && (
        <div
          className="instance-bar"
          role="progressbar"
          aria-label="可调度账号比例"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={available}
        >
          <i style={{ width: `${Math.round(ratio * 100)}%` }} />
        </div>
      )}
    </div>
  );
}

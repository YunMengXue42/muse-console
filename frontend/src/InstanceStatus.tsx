import { useEffect, useRef, useState } from "react";
import type { Status } from "./types";
import "./instance-status.css";

export type InstanceTone =
  "online" | "warn" | "offline" | "unreachable" | "pending";

const COPY: Record<InstanceTone, string> = {
  online: "驱动在线",
  warn: "需要关注",
  offline: "驱动异常",
  unreachable: "服务无响应",
  pending: "连接中",
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

/** 侧栏实例状态卡：状态色 + 动效 + 账号可用度 + 同步时间；状态切换时闪烁一次。 */
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
  const ratio = total > 0 ? Math.min(1, available / total) : 0;
  const running = status?.tasks?.running ?? 0;
  const hint =
    tone === "unreachable"
      ? "无法连接管理接口，自动重试中"
      : tone === "offline"
        ? "浏览器驱动未就绪，请求会失败"
        : tone === "warn"
          ? "没有可调度账号，新请求会排队"
          : tone === "pending"
            ? "等待服务响应"
            : total === 0
              ? "尚未接入账号"
              : running > 0
                ? `${running} 个任务运行中`
                : "一切正常";

  return (
    <div
      className={`instance-card tone-${tone}${flash ? " is-flash" : ""}`}
      data-tone={tone}
    >
      <div className="instance-status" role="status">
        <span className="instance-signal" aria-hidden="true">
          <span
            aria-hidden="true"
            className={`dot ${tone === "online" ? "online" : tone === "warn" ? "warn" : tone === "pending" ? "pending" : "down"}`}
          />
        </span>
        <div className="instance-copy">
          <span className="instance-state">{COPY[tone]}</span>
          {status && !unreachable && (
            <span className="instance-meta">
              <span>{status.driver.driver}</span>
              <span className="instance-version">v{status.version}</span>
            </span>
          )}
        </div>
      </div>
      <p className="instance-hint">{hint}</p>
      {status && total > 0 && (
        <div className="instance-capacity">
          <div className="instance-capacity-row">
            <span>可调度账号</span>
            <strong>
              {available}
              <span> / {total}</span>
            </strong>
          </div>
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
        </div>
      )}
      {lastSyncAt != null && (
        <span className="instance-sync" aria-hidden="true">
          {ago(now - lastSyncAt)}
        </span>
      )}
    </div>
  );
}

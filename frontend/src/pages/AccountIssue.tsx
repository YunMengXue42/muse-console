import type { Account } from "../api";
import { time } from "../api";

const REASONS: Record<string, [string, string]> = {
  composer_ready_timeout: [
    "输入框未就绪，发送超时",
    "Muse 网页输入框未在限定时间内就绪，不代表账号失效。",
  ],
  upstream_timeout: [
    "等待上游响应超时",
    "请求未在限定时间内完成，不代表账号失效。",
  ],
  upstream_auth_failed: [
    "登录验证未通过",
    "上次请求的登录验证未通过，请结合当前账号状态判断。",
  ],
  upstream_quota_exhausted: [
    "上游额度不足",
    "上次请求因额度不足未完成，请检查额度或等待恢复。",
  ],
  media_missing: [
    "只收到文字，未取得媒体",
    "上游仅返回文字，没有取得所需图片或视频。",
  ],
  upstream_refused: [
    "上游未返回所需结果",
    "上游没有返回请求所需的结果，不等同于账号被封禁。",
  ],
  upstream_error: ["上游服务异常", "上游服务未正常完成请求，不代表账号失效。"],
  request_failed: [
    "请求失败（未记录具体原因）",
    "未记录具体原因，可到任务中心或请求日志查看对应记录。",
  ],
};

export function AccountIssue({ account }: { account: Account }) {
  const code = account.last_error_code;
  const hasIssue = Boolean(code || account.last_error);
  const invalid =
    account.status === "invalid" || account.effective_status === "invalid";
  if (invalid)
    return <span className="account-reauth">登录已失效，请重新登录</span>;
  if (!hasIssue) return null;
  // Older servers expose only a redacted sentence; never render arbitrary error text.
  const knownReason =
    code && Object.prototype.hasOwnProperty.call(REASONS, code)
      ? REASONS[code]
      : undefined;
  const [reason, hint] = knownReason ?? [
    "请求失败（未记录具体原因）",
    "旧记录未保存细分原因，可到任务中心或请求日志查看。当前可用性以状态列为准。",
  ];
  const timestamp = account.last_error_at;
  const date =
    typeof timestamp === "number" && Number.isFinite(timestamp) && timestamp > 0
      ? time(timestamp)
      : null;
  return (
    <details className="account-history">
      <summary>上次请求：{reason}</summary>
      <div className="account-history-detail">
        {date && <span>发生于 {date}</span>}
        <span>{hint}</span>
        <span>仅为上次请求记录，当前可用性看状态列。</span>
      </div>
    </details>
  );
}

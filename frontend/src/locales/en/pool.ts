export const pool: Record<string, string> = {
  "显示 {visible} / {total} 个账号":
    "Showing {visible} / {total} {total|account|accounts}",
  "{diffSec}s 后恢复": "Available in {diffSec}s",
  "{m}m {s}s 后恢复": "Available in {m}m {s}s",
  "{m}m 后恢复": "Available in {m}m",
  全部: "All",
  可用: "Available",
  需关注: "Needs attention",
  账号池概览: "Account pool overview",
  账号总数: "Total accounts",
  已接入的上游会话: "Connected upstream sessions",
  可调度: "Ready",
  可以接收新请求: "Can accept new requests",
  占用槽位: "Slots in use",
  "正在执行 · {n} 个请求排队中": "Running · {n} {n|request|requests} queued",
  正在执行的并发请求: "Concurrent requests running",
  需要关注: "Needs attention",
  "失效、冷却或停用": "Invalid, cooling down, or disabled",
  上游账号列表: "Upstream account list",
  上游账号: "Upstream accounts",
  "就绪会话自动参与调度。": "Ready sessions are scheduled automatically.",
  "{n} 个账号": "{n} {n|account|accounts}",
  账号状态筛选: "Filter accounts by status",
  搜索账号: "Search accounts",
  "搜索名称或账号 ID": "Search by name or account ID",
  调度策略: "Scheduling strategy",
  调度策略已更新: "Scheduling strategy updated",
  轮询: "Round robin",
  最少最近使用: "Least recently used",
  会话亲和: "Session affinity",
  "账号表格，可横向滚动": "Account table, scroll horizontally",
  账号标识: "Account",
  状态: "Status",
  并发: "Concurrency",
  "成功 / 失败": "Success / Failure",
  最近使用: "Last used",
  操作: "Actions",
  未命名账号: "Unnamed account",
  "余量 {remaining}": "Remaining {remaining}",
  "积分 {credits}": "Credits {credits}",
  独立上限: "Custom limit",
  编辑: "Edit",
  账号已停用: "Account disabled",
  账号已启用: "Account enabled",
  停用: "Disable",
  启用: "Enable",
  当前驱动不支持会话保活: "The current driver does not support session renewal",
  保活请求已完成: "Session renewal completed",
  保活: "Keep alive",
  状态已重置: "Status reset",
  重置: "Reset",
  合并: "Merge",
  "删除账号「{n}」？此操作不可撤销。":
    'Delete account "{n}"? This cannot be undone.',
  删除: "Delete",
  没有匹配的账号: "No matching accounts",
  "更换名称、ID 或清除筛选。": "Try another name or ID, or clear the filters.",
  清除筛选: "Clear filters",
  暂无账号: "No accounts yet",
  登录上游账号: "Sign in to an upstream account",
  "并发上限 {n} · 冷却 {n1}s · 获取超时 {n2}s":
    "Concurrency limit {n} · Cooldown {n1}s · Acquire timeout {n2}s",
  调度配置等待同步: "Waiting for scheduling configuration",
  接入说明: "Setup guide",
  "连接账号 → 验证会话 → 分配密钥":
    "Connect accounts → Verify sessions → Assign keys",
  连接上游账号: "Connect upstream accounts",
  "逐号网页登录，或批量导入 Cookie。":
    "Sign in to each account on the web, or import cookies in bulk.",
  验证会话状态: "Verify session status",
  "可调度后即可使用；验证码需手动完成。":
    "Ready accounts can be used immediately; complete CAPTCHAs manually.",
  分配调用密钥: "Assign API keys",
  "为应用创建独立 API Key。": "Create a separate API key for each application.",
  "管理 API 密钥 →": "Manage API keys →",
  "仅接入有权使用的账号；Cookie 是敏感凭据，勿公开分享。":
    "Only connect accounts you are authorized to use. Cookies are sensitive credentials; do not share them publicly.",
  "上次请求的输入框未能及时就绪，请稍后重试":
    "The input field was not ready in time for the last request. Try again later.",
  "上次请求等待上游响应超时，请稍后重试":
    "The last request timed out waiting for the upstream response. Try again later.",
  "上次上游认证失败，请检查或更新账号凭据":
    "Upstream authentication failed. Check or update the account credentials.",
  "上次请求的上游额度不足，请等待额度恢复":
    "The last request exceeded the upstream quota. Wait for it to recover.",
  "上次请求仅收到文本，未获得所需图片或视频":
    "The last request returned only text, without the required image or video.",
  "上次上游未返回所需结果，请调整请求后重试":
    "The upstream service did not return the required result. Adjust the request and try again.",
  "上次请求发生上游服务错误，请稍后重试":
    "The last request encountered an upstream service error. Try again later.",
  "上次请求失败，请检查请求或稍后重试":
    "The last request failed. Check the request or try again later.",
  "输入框未就绪，发送超时": "Input field not ready; send timed out",
  "Muse 网页输入框未在限定时间内就绪，不代表账号失效。":
    "The Muse input field was not ready in time. This does not mean the account is invalid.",
  等待上游响应超时: "Upstream response timed out",
  "请求未在限定时间内完成，不代表账号失效。":
    "The request did not finish in time. This does not mean the account is invalid.",
  登录验证未通过: "Authentication failed",
  "上次请求的登录验证未通过，请结合当前账号状态判断。":
    "Authentication failed on the last request. Check the current account status.",
  上游额度不足: "Upstream quota exhausted",
  "上次请求因额度不足未完成，请检查额度或等待恢复。":
    "The last request could not finish due to insufficient quota. Check the quota or wait for recovery.",
  "只收到文字，未取得媒体": "Text received, but no media",
  "上游仅返回文字，没有取得所需图片或视频。":
    "The upstream service returned only text, without the required image or video.",
  上游未返回所需结果: "Required result not returned",
  "上游没有返回请求所需的结果，不等同于账号被封禁。":
    "The upstream service did not return the required result. This does not mean the account is banned.",
  上游服务异常: "Upstream service error",
  "上游服务未正常完成请求，不代表账号失效。":
    "The upstream service did not complete the request. This does not mean the account is invalid.",
  "请求失败（未记录具体原因）": "Request failed (no specific reason recorded)",
  "未记录具体原因，可到任务中心或请求日志查看对应记录。":
    "No specific reason was recorded. Check Tasks or Request log for details.",
  "登录已失效，请重新登录": "Session expired. Sign in again.",
  "旧记录未保存细分原因，可到任务中心或请求日志查看。当前可用性以状态列为准。":
    "Older records have no detailed reason. Check Tasks or Request log. Current availability is shown in the status column.",
  "上次请求：{reason}": "Last request: {reason}",
  "发生于 {date}": "Occurred at {date}",
  "仅为上次请求记录，当前可用性看状态列。":
    "This is the last request record only. See the status column for current availability.",
};

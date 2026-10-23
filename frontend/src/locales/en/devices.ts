export const devices: Record<string, string> = {
  "服务未开启凭据导入（404），需管理员开启。账号池其他功能不受影响。":
    "Credential import is disabled (404). Ask an administrator to enable it. Other account pool features are unaffected.",
  "设备列表读取失败，请重试。": "Could not load devices. Please try again.",
  "操作失败，请检查设备、候选和账号后重试。":
    "Operation failed. Check the device, candidate, and account, then try again.",
  扩展设备配对: "Extension device pairing",
  "扩展会把浏览器里的 Muse 登录交给本服务器。授权前核对下面三项与扩展一致；扩展拿不到管理员 Key。":
    "The extension shares your browser's Muse login with this server. Before authorizing, check that the three items below match the extension. The extension cannot access the admin key.",
  服务地址: "Server address",
  生成配对挑战后显示: "Shown after generating a pairing challenge",
  "服务器 ID": "Server ID",
  "应用指纹（非 TLS 证书指纹）":
    "Application fingerprint (not a TLS certificate fingerprint)",
  "指纹不能代替 HTTPS 证书校验。":
    "A fingerprint does not replace HTTPS certificate validation.",
  一次性配对: "One-time pairing",
  "配对码短时有效、只能用一次，关窗即隐藏。额度＝该设备最多自动新增的账号数。":
    "The pairing code expires shortly, works once, and is hidden when this window closes. The quota is the maximum number of accounts this device can add automatically.",
  "配对设备备注（可选）": "Device label (optional)",
  "例如：办公主力浏览器": "For example: primary work browser",
  "自动新增账号额度（0–10）": "New account quota (0–10)",
  生成配对挑战: "Generate pairing challenge",
  一次性配对挑战: "One-time pairing challenge",
  "剩余 {seconds}s（{expiry} 到期）。手动填入扩展，勿外传。":
    "{seconds}s remaining (expires {expiry}). Enter it in the extension manually. Do not share it.",
  "配对挑战已过期，请重新生成。":
    "Pairing challenge expired. Generate a new one.",
  已配对设备: "Paired devices",
  刷新设备列表: "Refresh devices",
  "正在读取设备…": "Loading devices…",
  "暂无已配对设备。": "No paired devices yet.",
  "设备 ID": "Device ID",
  备注: "Label",
  到期时间: "Expires",
  状态: "Status",
  新增账号额度: "New account quota",
  操作: "Actions",
  已撤销: "Revoked",
  已过期: "Expired",
  有效: "Active",
  未提供: "Not provided",
  "撤销设备 {id}": "Revoke device {id}",
  身份冲突确认: "Confirm account identity conflict",
  "扩展提示冲突时使用：填它给的 capture_id，选设备和目标账号。只对这一次生效。":
    "Use this when the extension reports a conflict: enter its capture_id, then select the device and target account. Applies only to this candidate.",
  "设备 device_id": "Device device_id",
  选择有效设备: "Select an active device",
  "候选 capture_id": "Candidate capture_id",
  "目标 account_id": "Target account_id",
  选择现有账号: "Select an existing account",
  核对本次候选: "Review this candidate",
  操作确认: "Confirm action",
  "确认撤销设备？": "Revoke this device?",
  "确认本次身份冲突？": "Confirm this identity conflict?",
  "设备 {id} 将不能继续提交凭据；不会删除已入池账号。":
    "Device {id} will no longer be able to submit credentials. Accounts already in the pool will not be deleted.",
  "请确认浏览器当前登录的就是该账号。只对本次候选生效，账号版本再变需重新确认。":
    "Confirm that the browser is currently signed in to this account. Applies only to this candidate; confirm again if the account version changes.",
  取消操作: "Cancel",
  "设备已撤销。": "Device revoked.",
  "已确认，尚未入池。请在扩展重传同一候选，服务端验证后写入。":
    "Confirmed, but not yet added to the pool. Resend the same candidate from the extension; the server will verify it before saving.",
  确认撤销: "Confirm revocation",
  仅确认本次候选: "Confirm this candidate only",
  关闭配对窗口: "Close pairing window",
};

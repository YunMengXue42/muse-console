# Muse Console 浏览器扩展

[English](README.md) · 简体中文 · **0.3.0** · Chrome / Edge 132+ · MV3

在浏览器里正常登录 Muse，扩展会把会话同步到**你自己部署**的 Muse Console。不用再手动导出 Cookie，会话轮换后也会自动更新。

<p>
  <img src="../docs/images/extension-popup.png" alt="扩展弹出面板" width="300" />
</p>

扩展界面跟随浏览器语言，支持中文和英文。

## 工作方式

```text
你登录 muse.ai ──> 扩展只读 4 个会话 Cookie ──> 已配对的 Muse Console
                                                  │ 临时浏览器验证会话
                                                  └ 通过后加密写入账号池
```

- **只读、最小范围**：只读取 `muse.ai` 的 `hatch_sess`、`hatch_gw`、`hatch_vml`、`hatch_native_auth_device`；不写 Cookie，不注入页面脚本，不读其他站点。
- **一次配对，精确授权**：只向你在设置页填写并授权的那一个服务器 origin 发送数据。
- **服务端先验证再提交**：会话验证失败不会覆盖原有凭据。
- **设备令牌受限**：令牌只能上传候选会话，不能读取 Cookie、列出账号或调用管理接口；可随时在控制台撤销。

## 安装

暂未上架应用商店，请从源码构建或使用 Release 中的 zip。

```bash
cd extension
npm ci
npm test
npm run package     # 生成 dist/ 与 release/muse-console-extension-0.3.0.zip
```

打开 `chrome://extensions`（Edge 为 `edge://extensions`），开启**开发者模式**，点击**加载已解压的扩展程序**，选择 `extension/dist`。

> 建议使用单独的浏览器配置文件安装，只在其中登录要接入的 Muse 账号。

## 使用

先在服务端开启导入（默认关闭）：

```bash
MUSE_CONSOLE_DRIVER=browser
MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED=true
MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN=https://console.example.com
```

### 快速配对（0.3.0）

> 配对包含有**短期敏感的一次性挑战，勿外传**，使用后请清理剪贴板。**预填不是信任来源**：仍须与已经信任的控制台核对查询到的身份。配对不代表同意 Cookie 同步。

1. **在可信控制台生成**：打开“账号池 → 扩展设备配对”，按需填写设备备注与新增账号额度，然后点击“生成配对挑战”。
2. **复制配对包**：点击“复制快速配对包”。剪贴板访问失败时，选中只读 JSON 配对包并手动复制。不要复制管理员 Key，也不要把配对包发给他人。
3. **粘贴并预填**：打开扩展设置，在“快速配对包”中粘贴 JSON，点击“解析并预填”。此动作仅预填精确服务器 origin 与挑战，不请求主机权限、不确认身份，也不开启同步。本机 loopback HTTP 开发目标须在解析**之前**勾选开发模式。
4. **重新查询并核对身份**：点击“授权访问并查询身份”，确认授权的是预期目标。扩展重新查询身份；origin、`server_id` 或 `fingerprint` 与包不符时拒绝配对。用户仍须独立核对显示的身份与可信控制台一致；应用指纹不是 TLS 证书 pin。
5. **明确确认并配对**：完成核对后才勾选身份确认，点击“兑换挑战并配对”。扩展在兑换挑战前再次核对身份。配对成功后同步仍关闭。
6. **单独授权同步**：阅读并勾选 Cookie 同步同意说明，点击“开启自动同步”。如有需要，在该浏览器登录 muse.ai；成功导入后弹出面板显示“同步正常”。如遇身份冲突需管理员确认，按下方状态说明处理。

配对包过期或无效时，请重新生成并复制。已加载的包与挑战在过期、修改目标、主动清除或关闭页面时清除，不持久化。“清除配对包与挑战”可返回手动配对。清除本地输入不撤销已经复制到其他位置的挑战。

<details>
<summary>备选方式：手动填写 origin 与一次性挑战</summary>

1. 在可信控制台打开“账号池 → 扩展设备配对”并生成挑战，查看其 origin、`server_id` 和 `fingerprint`。
2. 在扩展设置清除已加载的配对包，填写该精确 origin，点击“授权访问并查询身份”，与可信控制台核对查询到的 ID 与指纹。
3. 仅将一次性挑战粘贴到挑战输入框，明确确认身份后点击“兑换挑战并配对”。挑战过期须重新生成。
4. 单独勾选 Cookie 同步同意说明并点击“开启自动同步”。仅配对不会开启同步。

</details>

### 配对包格式与限制

JSON 为 `schema: "muse-console-pairing"`、`version: 1`，包含 `origin`、`server_id`、`fingerprint`、`challenge` 与 Unix 秒数 `expires_at`。输入限制为 8 KiB、规范精确 HTTPS origin（仅显式开发模式允许 loopback HTTP）和最多 15 分钟剩余有效期；拒绝额外字段及错误类型。不使用外部消息、content scripts 或 URL 自动授权。

<p>
  <img src="../docs/images/extension-options.png" alt="扩展设置页" width="720" />
</p>

本地开发可勾选“开发模式”连接 `http://127.0.0.1:18610`，服务端同时设置 `MUSE_CONSOLE_CREDENTIAL_ALLOW_LOOPBACK_HTTP=true`。远程地址必须使用 HTTPS。

## 状态说明

| 弹出面板                | 含义                             | 处理                                             |
| ----------------------- | -------------------------------- | ------------------------------------------------ |
| 同步正常                | 会话已写入账号池                 | 无需操作                                         |
| 等待登录 Muse           | 未检测到有效会话                 | 在该浏览器登录 muse.ai                           |
| 等待管理员确认          | 账号身份不明或版本冲突           | 在控制台确认该候选，再点“管理员确认后重传原候选” |
| 上传遇到问题            | 网络或服务器错误，扩展会退避重试 | 检查服务器与网络                                 |
| 设备授权已撤销 / 已过期 | 令牌失效                         | 重新配对                                         |

每个设备默认只能新增 1 个账号，后续同一账号的会话更新会自动合并；切换到其他账号需要管理员确认。

## 停用与撤销

- **暂停同步**：停止上传并清除待传候选。
- **解除本地配对**：清除扩展内的配对信息。
- **彻底撤销**：在控制台撤销该设备，令牌立即失效。解除本地配对不会删除服务端账号。

## 权限说明

| 权限                            | 用途                                                     |
| ------------------------------- | -------------------------------------------------------- |
| `cookies` + `https://muse.ai/*` | 只读 Muse 会话 Cookie                                    |
| `webNavigation`                 | Muse 页面加载后触发采集                                  |
| `alarms`                        | 去抖与失败重试                                           |
| `storage`                       | 保存配对信息；原始会话只放在会话级存储，浏览器关闭即清除 |
| 可选主机权限                    | 只对你在设置页授权的服务器 origin 生效                   |

不申请 `tabs`、`scripting`、内容脚本或外部消息权限；无痕窗口中不运行。

## 开发

```bash
npm test                 # 单元测试（含权限与静态安全检查）
npm run build            # 类型检查 + 打包到 dist/
npm run package          # build 并生成可复现 zip 与 SHA256
```

源码：`src/worker.ts`（事件调度）、`src/collector.ts`（Cookie 采集）、`src/engine.ts`（配对、上传、重试）、`src/policy.ts`（origin 与 Cookie 白名单）、`src/status-view.ts`（状态文案）。

`preview.html` 可通过本地 HTTP 服务打开，查看禁用控件的静态界面，无需安装扩展。

设计细节见 [架构文档](../docs/ARCHITECTURE.zh-CN.md#扩展与凭据范围)，部署要求见 [部署指南](../docs/DEPLOYMENT.zh-CN.md#6-扩展配对分离且精确的授权)。

## 许可

MIT，与主项目一致。界面字体子集基于 Noto Sans CJK SC（SIL OFL 1.1），见 `fonts/`。

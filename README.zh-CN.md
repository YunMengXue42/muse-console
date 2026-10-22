# Muse Console

面向 [muse.ai](https://muse.ai) 网页应用的独立、非官方网关，提供 **OpenAI 风格 API 的一个子集**和 React 管理控制台。

[English（主文档）](README.md) | **简体中文**

版本 **0.4.2** · Python **3.10+** · MIT

## 目录

- [概览与状态](#概览与状态)
- [快速开始](#快速开始)
- [真实账号与凭据导入](#真实账号与凭据导入)
- [API 参考与示例](#api-参考与示例)
- [兼容性边界](#兼容性边界)
- [安全与运行边界](#安全与运行边界)
- [配置](#配置)
- [备份升级与验证](#备份升级与验证)
- [架构与开发](#架构与开发)
- [公开前检查](#公开前检查)
- [许可上游归属与商标](#许可上游归属与商标)

## 概览与状态

Muse Console 提供文本聊天、参考图生成、异步图像/视频任务、账号调度和管理控制台。客户端可使用自定义 `/v1` 地址。`gpt-4o`、`claude-sonnet-4`、`deepseek-chat`、`dall-e-3`、`sora` 等名称只是 **Muse 能力的路由别名，不代表接入这些供应商的真实模型**。新集成建议使用 `muse-chat`、`muse-image` 和 `muse-video`。

| 组件 | 已实现范围 / 状态 |
|---|---|
| 协议 | 普通/SSE 聊天、无状态 Responses 子集、图像生成、参考图编辑、视频任务、模型列表 |
| 账号池 | 默认轮询、LRU、亲和调度；单账号并发、冷却、失效会话排除、聊天输出前故障转移 |
| 控制台 | 账号增删改查/合并、批量导入预览、隔离登录、客户端密钥、请求日志/统计、任务列表、管理员 Prometheus 指标 |
| 持久化 | SQLite 加密账号载荷；本地任务历史、媒体所有权、签名下载链接及可选保留期清理 |
| Mock 驱动 | 无需账号的离线合成输出；模拟视频字节不是可播放的视频 |
| 浏览器驱动 | Chromium + CDP 实现；历史真实样本不代表本修订已验收 |
| Cookie 扩展 | 只读 MV3 导入、受限设备配对、加密接收；须明确安装并授权 |
| 续期 / 配额 | 续期默认关闭，尚未通过真实环境验收；配额查询依赖驱动，不支持时返回 501 |
| 直连 HTTP 驱动 | 预留，返回 501 |

离线测试不能证明真实登录/生成、长期续期、全部 SDK 兼容、Docker 运行验收或 GitHub Actions 已通过。变更见 [CHANGELOG.md](CHANGELOG.md)，待办见 [TODO.md](TODO.md)。本文不声称已有 PyPI 发行包或 Docker registry 镜像；下述安装均基于源码。

## 快速开始

### 前置条件

- 安装 **uv 0.12.11**，与 [CI](.github/workflows/ci.yml) 固定版本一致；参照 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)选择该版本，以 `uv --version` 确认。
- 使用 `uv python install 3.12` 安装 **Python 3.12**。软件声明支持 **Python 3.10+**；CI 定义了 Windows、Linux x64、Linux ARM 的 Python 3.10/3.12 任务。
- 安装 **Node.js 24**（前端/扩展开发推荐，与 CI 和 Docker 构建一致）及 npm。工作流定义不代表已经运行通过。
- Git；仅真实浏览器驱动需要 Chromium/Chrome。首次安装依赖和构建需要联网。

### 离线 Mock 服务

从新检出的源码开始；不要覆盖已有 `.env`，不要复用私密服务数据：

```bash
git clone https://github.com/YunMengXue42/muse-console.git
cd muse-console
uv --version
uv python install 3.12
uv sync --locked --extra dev --python 3.12
(cd frontend && npm ci && npm run build)
cp .env.example .env
uv run --no-sync muse-console
```

PowerShell 使用 `Copy-Item .env.example .env`，前端命令改用 `cd frontend` / `cd ..`，不用上述子 shell。uv 命令无需手动激活虚拟环境。前端构建将打包资源写入 `src/muse_console/web`。

示例配置选择 `mock` 并关闭续期。Mock 生成不访问 Muse；调用者传入的远程图片 URL 仍可能触发下载，因此离线检查应使用纯文本/本地测试数据。访问 `http://127.0.0.1:18610/dashboard`（别名 `/admin`）。未配置 API 密钥时，首次启动生成并保存到 `data/api_key`，管理员密钥默认与其相同，请妥善保密。通过 `http://127.0.0.1:18610/healthz` 和 `/readyz` 检查存活/就绪状态。

### 从源码构建 Docker

```bash
cp .env.example .env
docker compose up -d --build
```

Compose 构建本地标签 `muse-console:local`，持久化 `./data`，仅发布到 `127.0.0.1:18610`，不是从 registry 下载发行镜像。验收请使用新的测试数据。启用真实账号或远程访问前阅读[部署指南](docs/DEPLOYMENT.zh-CN.md)；上述命令是操作说明，不是已完成构建/运行的声明。

## 真实账号与凭据导入

### 隔离浏览器登录或手动导入

1. 安装 Chromium/Chrome，设置 `MUSE_CONSOLE_DRIVER=browser`，必要时设置 `MUSE_CONSOLE_CHROMIUM_PATH`。仅使用项目自有配置目录，默认 `data/browser-profile`，不得使用日常浏览器目录。新建/空目录通过 `.muse-console-owned-profile` 标记认领；有效的旧 `.muse2api-owned-profile` 标记仍受支持。非空且无标记的目录会被拒绝，不得通过复制或伪造标记绕过日常配置目录保护。
2. 使用管理员密钥登录控制台，选择 **网页登录添加**，操作隔离页面，**手动**完成密码、MFA 和验证码，再选择 **完成验证并入池**。服务端验证上游会话后才保存。Mock/HTTP 驱动的网页登录返回 501；登录控制不暴露 CDP 地址。
3. 也可选择 **批量导入**，输入 JSON 账号数组、浏览器 Cookie 导出、Netscape Cookie 文件或每行一个 Cookie 请求头。预览只检查结构并按 `hatch_sess` 去重，**不能证明登录有效**。必须提供 `hatch_sess`；可选 `hatch_gw`、`hatch_vml`、`hatch_native_auth_device` 存在时会被转发。

登录上下文默认存活 600 秒，最多同时 3 个窗口。`MUSE_CONSOLE_LOGIN_AUTO_COMPLETE_ENABLED=true` 可在**用户完成认证后**自动验证并导入，不是密码或验证码自动化。

### 可选 Chrome / Edge 扩展

扩展通过官方 cookies API 读取四种 Muse 会话 Cookie（含 HttpOnly 和明确查询的分区）。不会复制配置目录、修改/删除 Cookie、为日常浏览器开启调试，也不收集 Google/Meta 凭据。

1. 使用独立的浏览器驱动测试部署。启用 `MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED`，将 `MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN` 设为精确且受信任的 HTTPS origin，例如 `https://console.example.com`，末尾不加斜杠。只有明确设置 `MUSE_CONSOLE_CREDENTIAL_ALLOW_LOOPBACK_HTTP=true` 才允许回环 HTTP 开发，不允许局域网/公网 HTTP。
2. 依次运行 `cd extension`、`npm ci`、`npm test`、`npm run build`；在 Chrome/Edge 扩展开发界面自行加载 `extension/dist`。安装及权限必须经过你的同意。
3. 在控制台 **扩展设备配对** 中生成一次性挑战。在扩展选项中输入同一 origin、授予权限、查询身份并核对 `server_id` 和 `fingerprint`。该指纹是应用身份校验和，**不是 TLS 证书固定**。交换挑战时绝不要向扩展提供管理员密钥。
4. 单独同意自动同步，再正常登录。Cookie 稳定后，扩展发送单个存储区/分区快照；后端在项目自有临时上下文中探测 `/api/session`，验证后保存，不发起生成。界面与回执均脱敏。
5. 每个设备默认可新增一个账号。Cookie 轮换不能证明稳定身份：身份不明或服务端版本已推进时，管理员必须确认**确切采集记录与目标账号**，再重新提交原候选。此确认不授权未来账号切换；版本变化须重新确认，丢失的候选须重新采集。
6. 暂停/忘记只停止同步，不删除服务端账号。控制台撤销设备授权后可阻止后续上传。设备令牌可轮换、有有效期且权限受限，不能读取 Cookie、列出账号或调用管理 API。验证失败保留原凭据。

扩展秘密仅保存在 worker 内存/受信任会话存储中；受限设备令牌持久化于受信任扩展本地存储。服务端在 `credential-devices.db` 中保存设备令牌哈希，账号载荷加密保存在 `accounts.db`。脱敏回执上限为 30 天 / 10,000 行；来源绑定与配额高水位持久保留。**自动会话续期（含 72 小时可用性）尚未验证，默认关闭。** 带作用域凭据通过自有浏览器上下文续期，旧凭据通过 HTTP 续期；两者均不保证永久有效或跨设备/IP 可用。

## API 参考与示例

使用 `Authorization: Bearer <key>`。`/v1/*` 接受管理员密钥、配置的旧式 API 密钥或控制台创建且未撤销的客户端密钥。`/admin/*` 必须使用管理员密钥，普通客户端密钥不能管理服务。媒体也支持下文说明的限时签名链接。独立 API 文档建立前，详细参考保留在此。

在本地将 `KEY` 设为自己的密钥，不要贴到日志或问题报告中。以下为 Bash 示例：

```bash
# 流式聊天；省略 stream 或设为 false 可获得普通 JSON。
curl http://127.0.0.1:18610/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-chat","messages":[{"role":"user","content":"Hello"}],"stream":true}'

# 无状态 Responses 子集。
curl http://127.0.0.1:18610/v1/responses \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-chat","input":"Write a short poem","store":false}'

# 图像任务：使用返回的 id 轮询；省略 async 为同步输出。
curl http://127.0.0.1:18610/v1/images/generations \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-image","prompt":"A red fox","size":"16:9","async":true}'
curl http://127.0.0.1:18610/v1/images/generations/task_example \
  -H "Authorization: Bearer $KEY"

# 参考图生成，不是蒙版局部重绘。
curl http://127.0.0.1:18610/v1/images/edits \
  -H "Authorization: Bearer $KEY" \
  -F 'image[]=@reference.png' -F 'prompt=The same fox wearing a red scarf'

# 视频任务；也接受 JSON duration 或一致的 seconds。
curl http://127.0.0.1:18610/v1/videos \
  -H "Authorization: Bearer $KEY" \
  -F model=muse-video -F 'prompt=Maple leaves falling' -F seconds=5 \
  -F size=1280x720 -F first_frame=@reference.png
curl http://127.0.0.1:18610/v1/videos/task_example -H "Authorization: Bearer $KEY"
curl -L http://127.0.0.1:18610/v1/videos/task_example/content \
  -H "Authorization: Bearer $KEY" -o output.mp4
```

可选 `openai` SDK 是单独的客户端依赖，不是服务端启动条件：

```python
import os
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:18610/v1", api_key=os.environ["KEY"])
reply = client.chat.completions.create(
    model="muse-chat", messages=[{"role": "user", "content": "Hello"}]
)
print(reply.choices[0].message.content)
```

图像 JSON 的 `image` 接受一张或最多 4 张参考图，可为 data URL、公开 HTTP(S) URL 或纯 base64。`response_format` 为 `url` 或 `b64_json`；异步结果使用已存储媒体的 URL。可选本地透明抠图通过 `uv sync --locked --extra dev --extra matting --python 3.12` 安装，发送 `"background":"transparent"` 启用。它使用本地 BiRefNet/rembg，不是上游原生透明生成；首次会下载模型权重，CPU 开销可能较大。可选抠图依赖须单独做平台/运行验收。

### 端点索引

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/healthz`、`/readyz` | 存活 / 驱动及账号就绪状态 |
| GET | `/v1/models` | 正式模型 ID 与别名；未知名称返回 404 `model_not_found` |
| POST | `/v1/chat/completions`、`/v1/responses` | 聊天 / 无状态 Responses 子集 |
| POST / GET | `/v1/images/generations`、`/v1/images/generations/{id}` | 图像生成 / 任务轮询 |
| POST | `/v1/images/edits` | Multipart `image`/`image[]` + `prompt`；不支持 `mask` |
| POST / GET | `/v1/videos`、`/v1/videos/{id}`、`/v1/videos/{id}/content` | 创建 / 轮询 / 下载视频 |
| GET | `/v1/media/{name}` | 所有者/管理员密钥或签名链接下载 |
| GET | `/dashboard`、`/admin` | 控制台页面；管理 API 需要认证 |
| GET/POST/PATCH/DELETE | `/admin/accounts[/{id}]` | 账号管理 |
| POST | `/admin/accounts/{id}/renew`、`/reset`、`/merge` | 续期 / 重置 / 合并（`{"into":"acc_example"}`） |
| POST | `/admin/accounts/import` | `{content, preview}` 批量导入 |
| GET/PATCH | `/admin/pool` | 账号池状态与持久化调度策略 |
| GET | `/admin/status`、`/admin/tasks`、`/admin/metrics` | 状态 / 任务列表 / Prometheus 指标 |
| GET/POST/PATCH/DELETE | `/admin/keys[/{id}]` | 客户端密钥管理；创建时只显示一次秘密值 |
| GET | `/admin/requests`、`/admin/stats?window=1h\|24h\|7d` | 日志 / 聚合；支持密钥、账号、状态、路径、since、hide_polls 筛选 |
| POST/GET/DELETE | `/admin/login-sessions[/{id}]` | 创建 / 查询 / 取消登录 |
| GET / POST | `/admin/login-sessions/{id}/frame`、`/input`、`/complete` | 截图 / 白名单输入 / 验证导入 |
| GET/POST/DELETE | `/admin/credential-devices`、`/pairings`、`/{id}` | 设备列表 / 配对挑战 / 撤销 |
| POST | `/admin/credential-devices/{id}/confirm` | 确认 `{capture_id, account_id}` |
| GET / POST | `/credential-ingestions/server`、`/pair`、`/rotate` | 身份 / 挑战交换 / 设备令牌轮换 |
| POST / GET | `/credential-ingestions`、`/{capture_id}` | 候选导入 / 自己的脱敏回执；受限设备令牌，64 KiB 上限 |

表中缩写后缀沿用前一路由的前缀。示例任务 ID 和文件名是占位符，不是已存在的资源。

## 兼容性边界

| 接口 | 支持 | 不支持 / 不保证 |
|---|---|---|
| Chat | 全历史文本合并为提示词；system/developer/user/assistant 角色；用户图片；普通/SSE 输出；可选 usage 事件 | 工具/函数调用、音频/文件、结构化输出、强制 token 预算、停止序列、采样控制 |
| Responses | 文本/图片输入、instructions、metadata 回传、普通/SSE 事件、`store:false` | 保存响应、引用旧响应/会话、工具、后台执行、推理控制、JSON schema |
| Images | 依次生成 1–4 个结果、最多 4 张参考图、URL/base64 输出、可选本地抠图 | 蒙版重绘、风格/输出格式控制、非中性 quality |
| Video | 异步任务和可选单张参考帧 | 精确尺寸/时长、尾帧/视频/音频参考 |
| Models | Muse 能力 ID 和兼容别名 | 别名暗示的真实供应商模型 |

Chat/Responses 的未知控制参数或有实际语义的不支持值返回 400。接受 `temperature/top_p=1`、惩罚 `0`、`n=1`、空 tools、`tool_choice:auto/none`、纯文本输出、`store:false` 等中性默认值，不代表驱动能执行相应控制。图像 `quality` 仅接受 `auto`/空值（或省略），空 `background` 视为未设置。Schema 校验失败可能返回验证错误，而非协议控制参数的 400。

视频支持 JSON、multipart 和表单编码请求体。同时提供 `duration`/`seconds` 或 `image`/`input_reference` 时必须一致。Multipart 的 `first_frame` 或首个 `image[]` 作为参考；拒绝 `last_frame`、`video[]` 和 `audio[]`。`mode`、`resolution_name`、`generate_audio`、`watermark` 接受但忽略。尺寸/时长仅作为提示词提示，不保证上游精确执行。

Usage 按文本长度估算，不是精确 token 或上游计费，且不含图片成本。SSE 跟随网页更新，可能成块到达。默认先取得首块再发送 HTTP 200；可选 `MUSE_CONSOLE_STREAM_HEARTBEAT_INTERVAL>0` 会更早返回 200，后续错误只能在流内报告。已实现子集的官方 SDK 验收仍须单独完成。

## 安全与运行边界

- **密钥：** 分享 API 前设置独立 `MUSE_CONSOLE_ADMIN_KEY`，否则旧式 API 密钥同时拥有管理员权限。管理员可调用推理并查看全部任务/媒体。为不同调用者创建不同客户端密钥（部分集成称为 `clientKey`），不要分发管理员密钥。客户端密钥只显示一次，`keys.json` 仅保存哈希与前缀。
- **控制台/日志：** 控制台在 `sessionStorage` 中保存管理员密钥，退出时清除。`requests.db` 含密钥/模型/账号/状态/延迟/客户端 IP 元数据及轮询标记，应视为私密数据。转发 IP 头不是授权边界，须由可信代理处理。
- **会话隔离：** 复用命名空间由已认证密钥身份加可选 `user` 派生，同密钥/提示执行串行化。共享密钥即共享命名空间，但这不会清除 Muse 上游记忆；不要让互不信任的用户共享含私密记忆的账号。不提供上游账号租户隔离或按密钥计费/配额。
- **任务/媒体所有权：** 普通密钥读取其他所有者的任务/媒体返回 404；无所有者的旧记录仅管理员可读。媒体签名 URL 默认有效一小时，是可转发的持有者凭证。撤销已存储客户端密钥会使其签名链接失效；授权后的任务轮询会刷新链接。媒体响应为 `private, no-store`；备份须包含 `media-owners.db`、`media-signing.key`。
- **单 worker：** 每个数据目录只运行一个进程/worker。调度、浏览器租约、任务执行器和指标均为进程内状态；不支持多 worker 共用目录或分布式队列。重启会将未完成任务标为中断。
- **执行语义：** 媒体出错后不会自动换账号重放。超时/取消不能证明上游已停止、未扣费或已退款；重试前先检查上游结果。可选同账号“仅返回文本”视频重投不等于恰好执行一次，未实现多图部分结果恢复。
- **资源预算：** 默认请求体 12 MiB、16 个在途生成 HTTP 请求、32 个排队/运行任务、4 个执行器、每张输入图 8 MiB、每个输出 32 MiB、媒体总量 1 GiB、16 个生成标签页（空闲 900 秒清理）。单账号并发默认为 1。生成/队列/存储预算耗尽返回 429，请求体过大返回 413。这些是限制，不是吞吐承诺。存储满时不会静默删除，保留期清理须主动启用。
- **参考 URL：** 仅公开 HTTP(S) 栅格图片 MIME、标准端口；不允许 URL 凭据/片段、私网/链路本地/元数据地址及不安全重定向。校验 DNS 全部结果与每次跳转，连接固定到批准 IP 并保留主机名 TLS 验证。下载器忽略环境代理并限制传输/解压大小。MIME 检查不等于完整图片内容验证，仍须网络出口控制。
- **暴露面：** 管理面与 CDP 保持私有。使用可信 TLS 和受保护后端；转发头仅信任固定反向代理，不要使用 `forwarded_allow_ips="*"`。凭据接收检查最终 ASGI origin。应用没有全局 CORS 例外；如确有需要，在代理上仅对 `/v1/*` 放行精确客户端 origin，不要使用通配来源或开放控制台。见[部署指南](docs/DEPLOYMENT.zh-CN.md)。
- **公开纪律：** 不得将 `.env`、Cookie、密钥、数据目录、浏览器配置、对话/媒体、备份或未脱敏日志/截图放入 Git、交付压缩包、Issue 或 CI 附件。忽略文件和旧历史仍须检查。疑似泄露按 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 处理；只删除文件不能撤销秘密。

## 配置

支持环境变量或 `.env`；新部署使用 `MUSE_CONSOLE_`。核心管理的旧 `MUSE2API_` 名称仍可使用，新命名空间优先。全部配置/范围见 [.env.example](.env.example)、[config.py](src/muse_console/config.py)，部署说明见 [docs/DEPLOYMENT.zh-CN.md](docs/DEPLOYMENT.zh-CN.md)。

| 变量（均加 `MUSE_CONSOLE_` 前缀） | 默认值 | 含义 |
|---|---|---|
| `DRIVER` | `mock` | `mock` / `browser` / 预留 `http` |
| `HOST`、`PORT` | `127.0.0.1`、`18610` | 监听地址/端口 |
| `DATA_DIR` | `data` | 私密持久化根目录 |
| `API_KEY`、`ADMIN_KEY` | 自动生成、同 API 密钥 | 旧式推理密钥 / 管理员凭据 |
| `PUBLIC_BASE` | 空 | 媒体基础 URL；空时从请求派生 |
| `REQUEST_LOG_RETENTION_DAYS`、`MEDIA_RETENTION_DAYS` | `14`、`0` | 日志保留 / 可选媒体清理（0 禁用） |
| `MEDIA_URL_TTL` | `3600` | 签名链接有效秒数 |
| `MEDIA_MAX_BYTES`、`BROWSER_MEDIA_MAX_BYTES` | 各 `33554432` | 输出 / 浏览器附件字节上限 |
| `MEDIA_STORAGE_MAX_BYTES`、`INPUT_IMAGE_MAX_BYTES` | `1073741824`、`8388608` | 媒体总量 / 单张输入图字节上限 |
| `REQUEST_MAX_BYTES`、`REQUEST_MAX_INFLIGHT` | `12582912`、`16` | 请求体字节 / 生成 HTTP 并发 |
| `TASK_MAX_PENDING`、`TASK_MAX_CONCURRENT` | `32`、`4` | 排队+运行任务 / 执行器数 |
| `BROWSER_MAX_TABS`、`BROWSER_IDLE_TTL` | `16`、`900` | 生成标签页数 / 空闲秒数 |
| `POOL_STRATEGY`、`POOL_ACQUIRE_TIMEOUT` | `round_robin`、`60` | 调度 / 等待账号秒数 |
| `ACCOUNT_MAX_CONCURRENCY`、`MAX_FAILOVER` | `1`、`2` | 单账号并发 / 聊天输出前故障转移次数 |
| `ACCOUNT_COOLDOWN` | 代码 `120`；示例 `30` | 冷却秒数；请核对生效配置 |
| `LOGIN_SESSION_TTL`、`LOGIN_SESSION_LIMIT` | `600`、`3` | 登录秒数 / 上下文数量 |
| `LOGIN_AUTO_COMPLETE_ENABLED` | `false` | 手动认证后自动验证/导入 |
| `CREDENTIAL_INGESTION_ENABLED`、`CREDENTIAL_SERVER_ORIGIN` | `false`、空 | 受限导入 / 精确接收 origin |
| `CREDENTIAL_ALLOW_LOOPBACK_HTTP` | `false` | 显式回环开发例外 |
| `CHROMIUM_PATH`、`BROWSER_PROFILE_DIR`、`BROWSER_PROXY` | 自动探测、数据目录内自有配置、空 | 浏览器程序 / 配置目录 / 仅浏览器代理 |
| `CHROMIUM_NO_SANDBOX` | `false` | 降低隔离强度；优先修复沙箱支持 |
| `CHAT_TIMEOUT`、`IMAGE_TIMEOUT`、`VIDEO_TIMEOUT` | `300`、`240`、`600` | 生成超时秒数 |
| `FIRST_TOKEN_TIMEOUT`、`STREAM_HEARTBEAT_INTERVAL` | `45`、`0` | 首次输出期限 / 可选 SSE 心跳秒数 |
| `VIDEO_DEADLINE_CAP`、`VIDEO_TEXT_ONLY_RETRIES` | `600`、`1` | 视频总预算 / 仅文本结果重投次数 |
| `KEEPALIVE_ENABLED`、`KEEPALIVE_INTERVAL` | `false`、`21600` | 未验收续期 / 间隔秒数 |
| `MATTING_MODEL` | `birefnet-general` | 可选本地抠图模型 |

## 备份升级与验证

`accounts.db` 的账号载荷由 `credentials.key` 加密：停止服务后，**数据库和密钥必须成对备份**。丢失密钥将无法恢复账号；同时取得两者的攻击者不受该加密保护。旧 `accounts.json` 导入时不会被修改/删除，可能继续包含明文秘密。限制文件系统权限（Windows 应使用 NTFS ACL，仅 `chmod` 不足）。

1. 停止**所有写入者**。备份完整数据目录及配套源码/镜像，包括账号/设备数据库、加密密钥、媒体所有权/签名文件和其他状态。备份按秘密保护，不得公开。
2. `uv run --no-sync python tools/backup_restore.py backup --data <stopped-data> --output <new-backup-dir> --stopped` 创建排他、哈希校验副本。工具不会替你停止服务，并拒绝已有目标、符号链接/reparse 路径、硬链接和 SQLite 辅助文件。
3. 在独立目录/端口上使用副本测试新修订，切换前检查就绪状态、管理员访问、导入预览、账号分配和经授权的真实请求，不得使用唯一生产副本测试。
4. 使用 `uv run --no-sync python tools/backup_restore.py restore --backup <backup-dir> --output <new-restore-dir> --stopped` 恢复。回滚必须恢复**匹配旧版本及完整升级前备份**，不能将旧 JSON 覆盖到新 SQLite 上。哈希用于发现损坏，不防恶意替换。

只读观察命令：`uv run --no-sync python tools/soak_check.py --base-url http://127.0.0.1:18610 --duration 10 --interval 1 --report <new-report.jsonl>`。远程 URL 必须 HTTPS；可选 `--admin-key-env <VARIABLE_NAME>` 添加脱敏计数。它不跟随重定向、不生成、不续期、不保存响应正文。仅有 72 小时健康报告不能证明凭据可用。部署的确切修订仍须分别验证真实行为、Docker、可选抠图和 CI。

## 架构与开发

依赖方向：`api → services → accounts/core → drivers → upstream`。路由负责认证/校验/SSE；网关租用账号，仅在输出前重试符合条件的聊天错误；驱动实现上游访问。异步应用中的持久化仍使用有界同步本地 I/O。

- `src/muse_console/`：应用/配置、协议路由、账号池/存储、网关/任务、Mock/浏览器/预留 HTTP 驱动及打包 `web/` 资源。
- `frontend/`：React/TypeScript 控制台；`extension/`：可选 MV3 导入器。
- `tests/`：离线服务、持久化、模拟 CDP 回归；`tools/`：公开边界检查、备份恢复、监测。
- [架构](docs/ARCHITECTURE.md)、[贡献指南](CONTRIBUTING.zh-CN.md)、[安全](SECURITY.zh-CN.md)、[部署](docs/DEPLOYMENT.zh-CN.md)、[任务板](TODO.md)。

在 Mock、续期关闭和合成数据环境下检查，不使用私密凭据：

```bash
uv lock --check
uv sync --locked --extra dev --python 3.12
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync python -m pytest
(cd frontend && npm ci && npm run lint && npm test && npm run build)
(cd extension && npm ci && npm test && npm run build)
```

使用 `python -m pytest`，不要使用直接 pytest 入口。扩展格式/类型检查及隔离环境见贡献指南。待办包括确切修订的真实回归、多账号负载/续期验收、直连 HTTP、SDK 子集验收及蒙版重绘；不承诺交付日期或响应 SLA。

## 公开前检查

准备文档**不代表授权**更改仓库可见性、推送代码或发布软件包/镜像。按[公开清单](docs/PUBLISHING.zh-CN.md)检查：

1. 检查已跟踪改动、全部本地 Git refs/历史及提交身份中的秘密和个人信息。另行检查忽略/未跟踪文件、交付压缩包、构建产物、截图和 CI 附件；示例不得包含真实账号信息、生产主机/IP 或本机绝对路径。
2. 在仓库根目录运行仅依赖标准库的[公开检查工具](tools/check_publication.py)：

   ```bash
   uv run --no-sync python tools/check_publication.py
   uv run --no-sync python tools/check_publication.py --history
   ```

   默认扫描已跟踪文件的工作区内容；`--history` 扫描全部本地 Git refs 可达的 blob。JSON 结果不输出匹配的秘密值，提交身份以哈希供另行审查。有限的模式/路径规则不等于安全审计、凭据有效性检查或公开认证；未跟踪/忽略文件、不可达对象、远程设置和外部产物不在检查范围内。
3. 发现泄露须撤销/轮换凭据，并在发布前复查历史和产物。核对实际测试结果、尚未完成的验收及 MIT 声明保留情况；扫描或 CI guard 无告警本身不能作为公开放行依据。

## 许可上游归属与商标

Muse Console 派生自 MIT 许可的 [www222fff/muse2api](https://github.com/www222fff/muse2api)。[LICENSE](LICENSE) 保留 `Copyright (c) 2026 www222fff` 及 MIT 许可声明。副本/实质性部分、软件包和容器镜像必须保留这些声明。改名不转移版权，也不代表上游背书；上游的 stars、发行记录和维护者不能冒充本项目的对应信息。

本项目由 [YunMengXue42/muse-console](https://github.com/YunMengXue42/muse-console) 独立维护。发行包/CLI 名称为 `muse-console`，Python 模块为 `muse_console`。Muse/muse.ai、OpenAI 及其他产品/模型名称归各自权利人所有，仅用于标识集成/兼容目标，不代表关联或背书。请遵守上游条款、账号授权和适用法律。MIT 软件按原样提供，不保证安全性、可用性或兼容性。

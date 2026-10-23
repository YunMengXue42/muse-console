# API 与配置参考

[English](API.md) · [项目首页](../README.zh-CN.md)

## API 参考与示例

使用 `Authorization: Bearer <key>`。`/v1/*` 接受管理员密钥、配置的旧式 API 密钥或控制台创建且未撤销的客户端密钥。`/admin/*` 必须使用管理员密钥，普通客户端密钥不能管理服务。媒体也支持下文说明的限时签名链接。

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
- **暴露面：** 管理面与 CDP 保持私有。使用可信 TLS 和受保护后端；转发头仅信任固定反向代理，不要使用 `forwarded_allow_ips="*"`。凭据接收检查最终 ASGI origin。应用没有全局 CORS 例外；如确有需要，在代理上仅对 `/v1/*` 放行精确客户端 origin，不要使用通配来源或开放控制台。见[部署指南](../docs/DEPLOYMENT.zh-CN.md)。
- **公开纪律：** 不得将 `.env`、Cookie、密钥、数据目录、浏览器配置、对话/媒体、备份或未脱敏日志/截图放入 Git、交付压缩包、Issue 或 CI 附件。忽略文件和旧历史仍须检查。疑似泄露按 [SECURITY.zh-CN.md](../SECURITY.zh-CN.md) 处理；只删除文件不能撤销秘密。

镜像默认 `browser` 和 `MUSE_CONSOLE_CHROMIUM_NO_SANDBOX=true`；源码默认仍为 `mock` 和 `false`。浏览器漏洞可能暴露容器内数据，容器限制不等价于 Chromium 沙箱。容器方案须保留非 root、cap-drop ALL、no-new-privileges、init 和 loopback 绑定；禁止暴露 CDP、挂载 Docker socket 或 privileged。续期默认关闭，长期效果未验证。

## 配置

支持环境变量或 `.env`；新部署使用 `MUSE_CONSOLE_`。核心管理的旧 `MUSE2API_` 名称仍可使用，新命名空间优先。全部配置/范围见 [.env.example](../.env.example)、[config.py](../src/muse_console/config.py)，部署说明见 [docs/DEPLOYMENT.zh-CN.md](../docs/DEPLOYMENT.zh-CN.md)。

| 变量（均加 `MUSE_CONSOLE_` 前缀） | 默认值 | 含义 |
|---|---|---|
| `DRIVER` | 源码 `mock`；镜像 `browser` | `mock` / `browser` / 预留 `http` |
| `HOST`、`PORT` | 源码 `127.0.0.1`、镜像 `0.0.0.0`；`18610` | 监听地址/端口；容器端口仅发布至宿主 loopback |
| `DATA_DIR` | 源码 `data`；镜像 `/app/data` | 私密持久化根目录 |
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
| `CHROMIUM_NO_SANDBOX` | 源码 `false`；镜像 `true` | 镜像关闭沙箱；浏览器漏洞可能暴露容器内数据，容器限制不等价于浏览器沙箱 |
| `CHAT_TIMEOUT`、`IMAGE_TIMEOUT`、`VIDEO_TIMEOUT` | `300`、`240`、`600` | 生成超时秒数 |
| `FIRST_TOKEN_TIMEOUT`、`STREAM_HEARTBEAT_INTERVAL` | `45`、`0` | 首次输出期限 / 可选 SSE 心跳秒数 |
| `VIDEO_DEADLINE_CAP`、`VIDEO_TEXT_ONLY_RETRIES` | `600`、`1` | 视频总预算 / 仅文本结果重投次数 |
| `KEEPALIVE_ENABLED`、`KEEPALIVE_INTERVAL` | `false`、`21600` | 未验收续期 / 间隔秒数 |
| `MATTING_MODEL` | `birefnet-general` | 可选本地抠图模型 |


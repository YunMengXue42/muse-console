# 部署 Muse Console

[English（canonical，权威版本）](DEPLOYMENT.md) | 简体中文译文；如有歧义，以英文为准。

本文是部署操作与验收要求，不代表已经完成部署或安全审计。先用获授权的源码副本、隔离数据和合成凭据。不预设或宣称有已发布的 PyPI 版本或 Docker registry 镜像，请本地构建源码。本文不授权公开或推送。

## 1. 本地 mock 基线

使用 Python 3.12、uv 0.12.11 和 Node.js 24。在仓库根目录将 `.env.example` 复制到**新的** `.env`，不要覆盖已有配置（PowerShell：`Copy-Item .env.example .env`）。检查文件与进程环境：`MUSE_CONSOLE_` 优先于旧 `MUSE2API_` 名称；同一命名空间中进程环境优先于 `.env`。

```bash
uv sync --locked --python 3.12
cd frontend
npm ci
npm run build
cd ..
uv run --no-sync muse-console
```

全新验收环境显式使用：

```dotenv
MUSE_CONSOLE_HOST=127.0.0.1
MUSE_CONSOLE_PORT=18610
MUSE_CONSOLE_DRIVER=mock
MUSE_CONSOLE_DATA_DIR=data-mock
MUSE_CONSOLE_KEEPALIVE_ENABLED=false
MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED=false
MUSE_CONSOLE_LOGIN_AUTO_COMPLETE_ENABLED=false
```

通过 `http://127.0.0.1:18610/healthz` 检查存活，`/readyz` 检查就绪，`/dashboard`（也可 `/admin`）访问控制台。使用管理 Bearer key 检查 `/admin/status`。Mock 结果是合成数据，无需上游账号；健康/就绪不证明真实认证或生成成功。若运行元数据不应公开，在代理限制健康端点访问。

## 2. 本地容器构建

Dockerfile 构建前端、安装锁定的 Python 包、包含 LICENSE，以 UID/GID 1000 运行并启动一个 `muse-console` 进程。Compose 构建 `muse-console:local`，仅绑定宿主机 `127.0.0.1:18610`，将 `./data` 挂载到 `/app/data`，并设置 `shm_size: 1gb`。

启动前创建专用 `./data`，所有者为容器服务身份，权限严格限制。POSIX 目录通常为 0700，秘密文件为 0600；Windows 应配置严格 ACL 并核验 Docker 挂载访问。不要用全员可写、特权或 root 运行解决权限问题。Compose 将 `MUSE_CONSOLE_DATA_DIR` 覆盖为 `/app/data`，源码 mock 示例中的 `data-mock` 不会改变宿主机挂载。隔离容器测试应使用独立源码目录或审阅过的本地 override，不能复用生产数据。

私下配置不同的秘密 key 后，POSIX shell 示例：

```bash
MUSE_CONSOLE_DRIVER=mock MUSE_CONSOLE_KEEPALIVE_ENABLED=false docker compose up -d --build
```

PowerShell 对应命令：

```powershell
$env:MUSE_CONSOLE_DRIVER = 'mock'
$env:MUSE_CONSOLE_KEEPALIVE_ENABLED = 'false'
docker compose up -d --build
```

Compose 显式 `environment` 项会覆盖 `env_file` 中的 driver 和 keepalive。不能仅靠 `.env` 作为可靠的启用方式；真实 driver 需要显式导出所需 shell 值。镜像在**容器内部**监听所有接口，仍须保留宿主机 loopback 端口映射和防火墙。绝不发布 CDP 19210 端口。构建时下载基础镜像/工具不等于存在已发布的 Muse Console 镜像。

## 3. 单 worker 与专属真实浏览器

**每个数据目录/profile 只允许一个 ASGI worker/进程和一个写入者**。禁止增加 worker、共享卷副本、重叠的 cron 写入或 reload 进程。账号池、任务、配对、预算与浏览器 context 包含进程内状态，SQLite 不是分布式调度器。重启会将未完成任务标记为 interrupted，而非重放。

`mock` 为离线 driver；`browser` 是真实 Chromium/CDP 实现；`http` 为预留项，未实现操作返回 501，不能当作生产后备。明确授权的真实账号测试步骤：

1. 停止 mock 实例，选择独立私有数据目录和**全新空的专属 profile**。设置 `MUSE_CONSOLE_DRIVER=browser`，必要时用 `MUSE_CONSOLE_CHROMIUM_PATH` 指定已安装的 Chromium/Chrome。默认 profile 为 `data_dir/browser-profile`；仅可将 `MUSE_CONSOLE_BROWSER_PROFILE_DIR` 改为另一个获授权的项目专属目录。
2. 禁止使用、复制、伪造所有权标记或读取用户日常主浏览器 profile 文件。首次启动以 `.muse-console-owned-profile` 认领空目录，也识别有效旧标记 `.muse2api-owned-profile`。非空且无标记目录会被拒绝。应使用新目录或明确授权的迁移，不得伪造标记。受保护根目录与 symlink/junction/reparse 检查只防误用，不是 OS 沙箱。
3. 使用专属非特权 OS 身份，只允许该身份访问 profile/数据。保持 Chromium sandbox 启用（`MUSE_CONSOLE_CHROMIUM_NO_SANDBOX=false`），修复 sandbox/user namespace 支持，不要通过 root 或禁用 sandbox 绕过。实现会在 root 运行时添加 `--no-sandbox`，因此 false 设置不能让 root 变安全。
4. 在管理控制台使用隔离的手动浏览器会话，自行完成认证/MFA，再完成验证与导入。不承诺密码或验证码自动化。批量 cookie 导入预览只验证结构，不证明上游会话有效。所有导入文件都按秘密处理。
5. 使用授权账号和预算，分别验证登录及所需的聊天/图片/视频能力。历史样本或离线测试不等于本次修订的真实验收。上游账号级记忆可能跨对话生效，不要让互不信任的租户共享一个上游账号。

服务只启动自己拥有的 Chromium，CDP 端口被占用时拒绝启动，不会附加未知浏览器。CDP 必须仅限 loopback，并由防火墙保护；禁止反向代理、转发、隧道或公开该端口。

## 4. TLS、代理信任与 CORS

远程访问必须经可信 TLS，例如 `https://example.com`。ASGI 后端只绑定 loopback/受保护 socket，且仅允许固定反向代理访问。代理必须强制预期 Host、覆盖转发的 scheme/host 信息并去除客户端提供的转发值。Uvicorn **只信任固定代理地址**，不能使用 `forwarded_allow_ips="*"`。简易 CLI 没有暴露这些代理参数；需要时显式使用 Uvicorn factory，例如代理在同一宿主机：

```bash
uv run --no-sync uvicorn muse_console.app:create_app --factory --host 127.0.0.1 --port 18610 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

代理发送 `Host: example.com` 和正确的 `X-Forwarded-Proto`。适当限制后端可达性，即使是 loopback。容器代理拓扑需要单独确定固定可信 peer，不能照搬宿主 loopback 示例。也可用 Uvicorn 直接 TLS，不得关闭证书验证。媒体链接设置 `MUSE_CONSOLE_PUBLIC_BASE=https://example.com`；启用导入时设置 `MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN=https://example.com`（精确 origin，无路径/尾斜杠）。导入层比较最终 ASGI origin，不信任任意原始转发头。在实际代理后验证此行为，再导入凭据。

控制台/管理接口保持同源。应用没有全局 CORS 放行。若独立浏览器客户端访问 `/v1/*`，仅在代理的该路由前缀配置 CORS：逐一列明精确 scheme/host/port origin，例如 `https://example.com:8443`；预检仅放行所需方法/请求头（`authorization`、`content-type`、`accept`），添加 `Vary: Origin`，错误响应也保留相关头。禁止全局 `*`、任意反射 origin、向跨域客户端开放管理接口，或把 CORS 当作认证。凭据导入路由有专门的窄范围扩展 origin/预检处理，不要用宽泛代理规则替换。分别验证允许与拒绝的 origin。SSE 需关闭代理响应缓冲，并为生成设置适当且有界的超时。

## 5. Key、存储与媒体

- **主/legacy key**：`MUSE_CONSOLE_API_KEY`；为空时首次启动生成并保存到 `data_dir/api_key`。保留在服务端，不分发给所有客户端。
- **管理 key**：单独生成并设置 `MUSE_CONSOLE_ADMIN_KEY`。为空时回退到主 key。管理身份具有更大权限，也能调用 API；使用相同值并不构成隔离。仅用于管理，禁止放入客户端或扩展。
- **客户端 key**：在控制台为不同客户端/所有者创建独立、可撤销的 key，仅用于 API，不用于管理。共享 key 等于共享所有者/对话命名空间。配对产生的设备 token 是第四种受限凭据，不与上述任一种混用。

本地独立生成高熵秘密，私下交付，`.env` 不入 Git/归档，代理/应用诊断中脱敏 Authorization 和 URL 查询签名。账号已加密也不能替代 TLS 和严格 OS 权限。

`accounts.db` 使用 `credentials.key` 加密完整账号载荷；key 缺失或错误会启动失败，不会覆盖原数据。这**不代表所有文件都已加密**：profile、生成媒体、请求元数据、任务、配置和旧 `accounts.json` 都需要保护。旧版迁移保留原始明文 JSON，需私下管理其留存。客户端 key 以哈希存储，`credential-devices.db` 保存设备 token 哈希及脱敏回执，但这些库仍含敏感元数据。按需使用加密磁盘/备份，对整个数据树及外置 profile 设置服务身份所有权/ACL。同时持有加密数据和 key 的人仍可解密。

媒体位于 `data_dir/media`；`media-owners.db` 记录 key 所有权，`media-signing.key` 签发有期限的 bearer URL。使用 key 下载需所有者/管理权限；持有签名 URL 的任何人可在有效期内访问。撤销存储的客户端 key 会使其签名链接失效，但到期/所有权不能阻止已下载副本。不要猜测旧文件归属、直接静态暴露媒体目录、在代理/CDN 缓存私有响应或记录 URL 签名。保留 private/no-store 行为。`MUSE_CONSOLE_MEDIA_URL_TTL` 默认 3600 秒；`MUSE_CONSOLE_MEDIA_RETENTION_DAYS=0` 表示关闭自动删除，应明确规划留存和磁盘清理。

## 6. 扩展配对：分离且精确的授权

导入默认关闭。使用明确授权的**独立测试浏览器**，不能自动安装到或检查用户主浏览器。扩展要求兼容 MV3 的 Chrome/Edge（manifest 最低 Chrome 132）。

1. 在独立 browser-driver 部署中设置 `MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED=true` 和精确的 `MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN=https://example.com`，使用可信 TLS。仅本地开发可由两端明确允许精确 loopback HTTP，如 `http://127.0.0.1:18610`，服务端设置 `MUSE_CONSOLE_CREDENTIAL_ALLOW_LOOPBACK_HTTP=true`。禁止远程/LAN 明文 HTTP。
2. 在 `extension` 中运行 `npm ci`、`npm test`、`npm run build`。只有用户同意安装/权限后，才手动加载 `extension/dist`。Manifest 的可选 HTTPS 权限范围不构成访问任意服务器的授权；只在扩展设置中授予选定目标权限。
3. 管理员在控制台生成一次性 pairing challenge。扩展设置输入完全相同的 origin、授予目标权限、读取服务器身份，并在交换 challenge 前与控制台核对 **`server_id` 和 `fingerprint` 两项**。Fingerprint 是应用身份校验和，**不是 TLS 证书 pin**。禁止向扩展输入管理/主/客户端 key。
4. 单独开启同步，并明确确认 Muse 会话凭据将发送到该服务器；正常登录 Muse。扩展只经 cookies API 只读采集 `hatch_sess`、`hatch_gw`、`hatch_vml`、`hatch_native_auth_device`，保留原 scope/store/partition。不复制 profile、不修改 cookie、不收集身份提供商密码。后端在自己拥有的临时浏览器 context 中探测固定 session 端点后才提交，不发送生成请求；导入成功不等于生成成功。
5. 设备默认可新增一个账号。已知上游 subject 可能匹配已有账号，但仅 cookie 轮换不能证明身份。身份缺失/冲突或服务端凭据版本更新时，可能需要管理员对**一个精确 capture、目标账号及当前凭据版本**进行确认。核对当前账号、确认该 capture，再重提原始候选。这不授权未来全部账号切换；版本变化需重新确认，候选丢失需重新采集和确认。
6. 暂停/忘记只停止本地同步，不删除服务端账号。需在服务端撤销设备授权以禁止未来上传；设备 token 有效期有限且可轮换，不能读取 cookie 值、列出账号或调用管理 API。验证失败保留已有凭据。原始 capture 仅临时留在可信扩展 session storage，受限设备 token 持久化在可信 local storage；独立浏览器本身也须保护。

## 7. 资源预算与续期验收

从保守配置开始，测量 CPU、RAM、磁盘、临时空间、队列延迟和上游配额。默认值包括：

| 设置后缀（前缀为 `MUSE_CONSOLE_`） | 默认值 |
| --- | --- |
| `REQUEST_MAX_BYTES` / `REQUEST_MAX_INFLIGHT` | 12582912 字节 / 16 |
| `TASK_MAX_PENDING` / `TASK_MAX_CONCURRENT` | 排队+运行共 32 / 运行 4 |
| `ACCOUNT_MAX_CONCURRENCY` / `BROWSER_MAX_TABS` | 每账号 1 / 生成标签页 16 |
| `LOGIN_SESSION_LIMIT` / `LOGIN_SESSION_TTL` | 3 / 600 秒 |
| `INPUT_IMAGE_MAX_BYTES` | 8388608 字节 |
| `MEDIA_MAX_BYTES` / `BROWSER_MEDIA_MAX_BYTES` | 各 33554432 字节 |
| `MEDIA_STORAGE_MAX_BYTES` | 1073741824 字节 |

登录和凭据验证 context 有独立上限；生成标签页上限不是 Chromium 总内存上限。额外设置 OS/容器 CPU、内存、进程、磁盘及出站限制；Compose 共享内存设置不是 RAM 上限。可选抠图增加模型下载、存储及 CPU/RAM 需求，需单独验证。仅公网下载验证是纵深防御，不替代出站控制。限制是进程内的，不是分布式计费限流或容量承诺。手动重试媒体生成前检查结果：不保证 exactly-once 生成、上游取消或退款。

`MUSE_CONSOLE_KEEPALIVE_ENABLED=false` 为默认值，获授权的真实验收前应保持关闭。带 scope 的凭据使用 owned-context 浏览器续期，旧凭据保留 HTTP 续期路径；两者都不保证跨设备/IP 可移植或永久登录。导入延迟目标、多账号负载及 **72 小时续期均未通过本次文档工作核验**。`MUSE_CONSOLE_LOGIN_AUTO_COMPLETE_ENABLED=false` 是另一个开关，仅用于结束隔离手动登录，不是无人值守密码/MFA 自动化。`tools/soak_check.py` 的只读健康监测不构成续期验收。

## 8. 停写一致备份与完整恢复

`tools/backup_restore.py` 不会停止服务、删除文件或覆盖目标。**先停掉全部写入者**：应用/容器、服务拥有的 Chromium、其他实例、续期/导入任务及维护脚本。停止接入流量，核对进程已停止。`--stopped` 是操作者的确认，不会自动加锁或停服务。

备份**完整的已停写数据目录**，不要挑选数据库：包括 `accounts.db` 与 `credentials.key`、`credential-devices.db`、`media-owners.db` 与 `media-signing.key`、媒体、`api_key`、`keys.json`、任务、账号池/运行时策略、请求历史及目录内专属 profile。外部 `.env`/秘密配置、外置专属 profile 和部署版本需在同一停写窗口另行保存，并同等严格保护。禁止包含主浏览器 profile。

若残留 SQLite `-wal`、`-shm` 或 `-journal` sidecar，工具会拒绝。不能删除它们强行通过：在全部写入者仍停止时，对各数据库执行适当的离线 SQLite 恢复/checkpoint，核对完整性和干净关闭后重试。Symlink/reparse 路径、硬链接/特殊文件和不可移植名称也会被拒绝，应调查而不是绕过。

在仓库根目录运行，输出目录必须**不存在、互不嵌套**且父目录已存在：

```bash
uv run --no-sync python tools/backup_restore.py backup --data data --output ../muse-backup-001 --stopped
uv run --no-sync python tools/backup_restore.py restore --backup ../muse-backup-001 --output ../muse-restored-001 --stopped
```

将 `data` 替换为实际已停写的宿主数据路径（所附 Compose 使用 `./data`）。源和目标不能相同或相互嵌套，即使目标是已存在的空目录也拒绝。失败保留部分输出供私下检查。工具创建严格权限（POSIX 0700/0600 或 Windows 当前用户 ACL），复制并验证清单/SHA-256。哈希仅检测损坏，**不防止清单和载荷一起遭恶意替换**；工具不加密备份，应另行加密并限制访问。

将整个已验证快照恢复到新目录；禁止合并到运行数据，或只恢复账号库而遗漏 key、所有权与设备状态。保持停写，恢复匹配的外部配置/profile，设置正确服务/容器所有权及 ACL（备份操作者可能不同于 UID 1000），再切换数据挂载/路径。保留旧目录用于私密回滚，不能让两个实例共享 profile/数据。在受限环境启动一个 worker，核对健康/就绪、管理/客户端 key 隔离、账号解密、设备撤销状态及媒体所有权/签名，再恢复流量。真实检查只使用授权测试账号，离线恢复不证明当前上游有效性。撰写本文没有执行恢复演练或完整真实验收。

## 9. 任何公开分发之前

检查暂存文件、完整 Git 历史和交付归档中的私密数据，`.gitignore` 不等于清理。排除所有 `.env` 文件、各种数据目录、备份、profile、key、日志、私密媒体、虚拟环境及 `node_modules`。泄漏秘密应轮换，不能只删除。副本/实质部分、包及镜像保留 [LICENSE](../LICENSE)、`Copyright (c) 2026 www222fff`、MIT 许可声明，以及 README 对 [www222fff/muse2api](https://github.com/www222fff/muse2api) 的上游归属。归属不表示上游背书。参见[贡献指南](../CONTRIBUTING.zh-CN.md)和[安全政策](../SECURITY.zh-CN.md)，注意私密报告可用性未确认及禁止公开漏洞的条件处理。公开、registry 上传和 Git 推送需另行明确授权。

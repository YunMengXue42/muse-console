# Muse Console

**把 Muse 网页能力接入现有 AI 工具，用一个控制台管理账号、密钥与生成任务。**

[English](README.md) · 简体中文 · **0.4.3** · Python **3.10+** · **MIT**

Muse Console 是面向 [muse.ai](https://muse.ai) 的独立、非官方网关，将聊天、图像和视频生成封装为 OpenAI 风格 API 子集。自托管，数据留在自己的服务器上。

![调用概览](docs/images/console-overview.png)

## 接入方案

```text
聊天客户端 / 脚本 / 画布
          ↓ OpenAI 风格 API
Muse Console：鉴权 · 账号调度 · 异步任务 · 媒体访问
          ↓ 专属 Chromium 浏览器
        muse.ai
```

前台通过 API 调用，后台通过可视化控制台维护。无需把浏览器自动化、账号调度和任务轮询分别接入客户端。

## 核心优势

| 优势 | 实现方式 |
|---|---|
| **统一接入** | 同一 `/v1` 地址提供聊天、参考图生成和视频任务；支持流式聊天及 Responses 子集 |
| **账号集中管理** | 网页登录、批量导入、可选 Cookie 扩展；轮询 / LRU / 亲和调度，按账号限制并发与冷却 |
| **过程可见** | 控制台集中查看账号状态、请求统计、任务进度与错误原因，提供 Prometheus 指标 |
| **访问可控** | 独立客户端密钥、任务与媒体归属校验、限时签名下载；账号载荷加密保存 |
| **便于开发** | Mock 模式无需账号即可联调；驱动分层，前端、协议与上游适配可独立维护 |

<details>
<summary>账号池截图</summary>

![账号池](docs/images/console-accounts.png)

</details>

## 快速开始

准备 **uv 0.12.11、Python 3.12、Node.js 24**，在新目录中执行：

```bash
git clone https://github.com/YunMengXue42/muse-console.git
cd muse-console
uv sync --locked --extra dev --python 3.12
(cd frontend && npm ci && npm run build)
cp .env.example .env
uv run --no-sync muse-console
```

打开 `http://127.0.0.1:18610/dashboard`。首次启动生成的密钥保存在 `data/api_key`。

默认使用 **Mock 模式**，无需账号。接入真实 Muse 时，安装 Chromium/Chrome，设置 `MUSE_CONSOLE_DRIVER=browser`，再从控制台网页登录或导入账号。密码、MFA 和验证码须手动完成。

PowerShell 用 `Copy-Item .env.example .env`，前端命令分步执行。不要覆盖已有配置或复用生产数据。[详细部署](docs/DEPLOYMENT.zh-CN.md)

## 接入示例

在客户端设置 API Base 为 `http://127.0.0.1:18610/v1`，使用控制台创建的客户端密钥。

```bash
curl http://127.0.0.1:18610/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-chat","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

建议使用 `muse-chat`、`muse-image`、`muse-video`。接口、参数与更多示例见 [API 参考](docs/API.zh-CN.md)。

## 浏览器扩展

配套的 MV3 扩展让账号接入更省事：在浏览器正常登录 Muse，会话自动同步到已配对的控制台，轮换后也会自动更新。扩展只读 4 个 Muse 会话 Cookie，只发往你授权的单一服务器，设备令牌可随时撤销。

安装与配对见 [扩展说明](extension/README.zh-CN.md)。

## 使用边界

- **兼容子集，不是完整 OpenAI 实现。** 模型别名不代表供应商真实模型；不支持工具调用、结构化输出或蒙版重绘。
- **单进程部署。** 每个数据目录运行一个 worker；自动续期默认关闭，长期续期与多账号负载仍待验收。直连 HTTP 驱动尚未完成。
- **保护真实凭据。** 使用专属浏览器目录；分享 API 前设置独立管理员密钥，为调用者创建客户端密钥。不要让互不信任的用户共享含私密记忆的上游账号。
- **完整备份。** 停止全部写入后备份整个数据目录，账号数据库与 `credentials.key` 必须一起保存。远程访问使用可信 TLS，不暴露 CDP。

当前提供源码构建，不提供已发布的 PyPI 包或容器镜像。真实生成依赖上游页面、账号状态与配额。

## 文档

[API 与配置](docs/API.zh-CN.md) · [浏览器扩展](extension/README.zh-CN.md) · [部署与备份](docs/DEPLOYMENT.zh-CN.md) · [架构](docs/ARCHITECTURE.zh-CN.md) · [贡献](CONTRIBUTING.zh-CN.md) · [安全](SECURITY.zh-CN.md)

[更新记录](CHANGELOG.md) · [待办](TODO.md) · [公开检查清单](docs/PUBLISHING.zh-CN.md)

## 许可与归属

基于 MIT 许可的 [www222fff/muse2api](https://github.com/www222fff/muse2api) 开发，由 [YunMengXue42](https://github.com/YunMengXue42/muse-console) 独立维护。再分发须保留 [LICENSE](LICENSE) 中的原作者版权与许可声明。

本项目与 Muse、OpenAI 及其他模型供应商无隶属或背书关系。请遵守上游条款与账号授权；软件按 MIT 条款原样提供。

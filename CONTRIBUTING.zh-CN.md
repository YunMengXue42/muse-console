# 参与 Muse Console 贡献

[English（canonical，权威版本）](CONTRIBUTING.md) | 简体中文译文；如有歧义，以英文为准。

## 环境与检查

在 Muse Console 仓库根目录操作（`https://github.com/YunMengXue42/muse-console`）；不预设仓库已公开或你已有访问权限。发行包/CLI 名为 `muse-console`，Python 包名为 `muse_console`，打包的 UI 资源位于 `src/muse_console/web`。

本地开发使用 **uv 0.12.11、Python 3.12、Node.js 24**，与 CI 工具版本对齐。项目声明最低 Python 3.10；CI 定义了 Windows、Linux x64、Linux ARM 上的 3.10/3.12 矩阵。工作流定义不代表实际运行已通过。

```bash
uv --version
uv lock --check
uv sync --locked --extra dev --python 3.12
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync python -m pytest

cd frontend
npm ci
npm run lint
npm test
npm run build
cd ..

cd extension
npm ci
node ../frontend/node_modules/prettier/bin/prettier.cjs --check "src/**/*.ts" "tests/**/*.ts" "*.json" "*.mjs" "*.html" "*.css"
node node_modules/typescript/bin/tsc --noEmit
npm test
npm run build
cd ..
```

在干净、隔离的环境中设置 `MUSE_CONSOLE_DRIVER=mock`、`MUSE_CONSOLE_KEEPALIVE_ENABLED=false` 后检查，不使用真实账号。CI 执行 `uv run --no-sync python -m pytest -q`，与上面的命令一样，明确使用所选环境的 Python。前端 `lint` 同时检查 TypeScript 和 Prettier；扩展 `build` 包含 TypeScript 检查，没有单独的 lint/format 脚本。扩展使用前述 `npm ci` 安装的前端锁定版 Prettier。有意修复格式时，可执行 `uv run --no-sync ruff format .`、在 `frontend` 执行 `npm run format`，或将扩展格式命令的 `--check` 改为 `--write`；随后审阅差异并重新检查。

`uv.lock` 是跨平台/Python 的通用锁文件，不是某台机器的快照。依赖更新需有意执行 `uv lock`、审阅差异并运行 `uv lock --check`，不得用 `pip freeze` 替换。可选抠图依赖需要单独的平台/运行时验收。

若预览正在占用常用虚拟环境，在**所有** sync/run 命令之前设置 `UV_PROJECT_ENVIRONMENT=.venv-hardening`（PowerShell：`$env:UV_PROJECT_ENVIRONMENT = '.venv-hardening'`）。不要重装或停止正在运行的预览环境。uv 命令无需手动激活虚拟环境。

## 本地开发

将 `.env.example` 复制成新的本地 `.env`，不要覆盖已有设置（POSIX：`cp .env.example .env`；PowerShell：`Copy-Item .env.example .env`），检查配置后再启动：

```bash
uv run --no-sync muse-console
# 或：
uv run --no-sync python -m muse_console
```

先构建前端。默认 `mock` driver 无需账号。新配置使用 `MUSE_CONSOLE_`；core 仍管理旧 `MUSE2API_` 别名，新名称优先。UI 开发可在 `frontend` 中执行 `npm run dev`，其 API 代理默认指向 `http://127.0.0.1:18610`。

使用真实凭据、浏览器 profile、扩展或反向代理前，先读[部署指南](docs/DEPLOYMENT.zh-CN.md)。扩展产物只能用于明确授权的独立浏览器，不代表可检查日常主浏览器 profile。

离线工具：

```bash
uv run --no-sync python tools/backup_restore.py --help
uv run --no-sync python tools/soak_check.py --help
```

备份/恢复要求先停掉所有写入者，并指定互不嵌套的路径。Soak 仅进行只读健康/就绪监测及可选的脱敏管理计数，不是生成或续期验收。真实测试不得指向生产数据或日常浏览器 profile。

## 认领任务

任务见 [TODO.md](TODO.md)。仓库权限允许时，创建标题为 `[Claim] <ID> <task name>` 的 issue，并在任务 Owner 列填入 GitHub ID，避免重复工作。`TODO(contributors)` 表示预留扩展点。漏洞不能通过公开任务 issue 报告，请遵循 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。

## 约定与 driver

- 依赖方向为 `api → services → accounts/core → drivers → upstream`，禁止向上导入。
- Muse 页面结构只放在 `drivers/browser/dom.py`，上游 URL 和 cookie 名只放在 `upstream/muse.py`（相对于 `src/muse_console`）。
- 新能力同时更新 `DriverCapabilities` 和假的 `MockDriver`，使测试无需网络。
- 上游错误映射到 `errors.py` 类型，不能在 driver 中静默吞掉异常。
- CI 不得访问真实 Muse；使用合成 fixture，而非含私密凭据或个人对话的录制数据。

添加 driver 时，在 `src/muse_console/drivers/<name>/` 实现 `MuseDriver`，在 `drivers/registry.py` 和 `config.DriverName` 注册，至少实现 `chat_stream`，尚不支持的能力继续抛出 `FeatureNotImplemented`，并补充离线测试与文档。

## 凭据与公开纪律

禁止提交或公开 `.env`、cookies、API/管理/客户端/设备 token、加密/签名 key、任何数据目录、浏览器 profile、私密对话、媒体、备份及未脱敏日志/截图。部署示例使用合成数据、`example.com` 或 loopback。既检查暂存区，也检查交付内容，不能只依赖 `.gitignore`：被忽略文件及旧 Git 历史仍可能随归档或公开操作泄漏。若凭据已泄漏，先撤销/轮换，再按 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 处理；只删除可见文件不够。

在仓库根目录运行离线公开前检查：

```bash
uv run --no-sync python tools/check_publication.py
uv run --no-sync python tools/check_publication.py --history
```

默认扫描受 Git 跟踪的工作树文件；`--history` 扫描**所有本地 Git refs** 可达的 blob，不仅是当前分支，并报告 commit 身份哈希供单独审阅。它只是有限模式/路径检查，不验证凭据有效性，也不是安全审计。不覆盖未跟踪/被忽略文件、不可达对象、远程 GitHub 设置或外部产物；无发现不等于准许公开。这些范围需另行检查，参见[公开指南](docs/PUBLISHING.zh-CN.md)。

不得将私密数据上传至 issue、PR、CI 产物或外部服务。本地构建/测试不构成公开仓库、包或镜像的授权；发布和推送必须另获明确批准。

## 打包与上游归属

前端构建后，可本地构建 wheel：

```bash
uv run --no-sync python -m pip wheel . --no-deps -w dist
```

此命令要求该解释器已安装 pip；若没有，请使用含 pip 的独立 Python 打包环境。检查 wheel 是否含 `muse_console/web/dashboard.html`、引用资源、CLI 入口和 MIT 许可声明。这些是验收要求，不代表某次发行已经通过。不预设或宣称有已发布的 PyPI 版本或 Docker registry 镜像，使用本地源码构建。

Muse Console 派生自 [www222fff/muse2api](https://github.com/www222fff/muse2api)。副本/实质部分、包及容器镜像必须保留 [LICENSE](LICENSE) 的 `Copyright (c) 2026 www222fff` 与 MIT 许可声明，并保留 README 上游归属。改名不转移版权、不表示上游背书，也不能将上游 stars、发行版和维护者当作本项目的。交付归档排除所有凭据、数据、profile、虚拟环境及 `node_modules`。

## 提交变更

- 分支名称示例：`feat/<module>`、`fix/<issue>`。
- 运行上面的相关检查，区分实际结果与尚未核验的验收项。
- 面向用户的变更适当补充 [CHANGELOG.md](CHANGELOG.md)。
- 英文为 canonical，同步更新对应中文翻译和双向语言链接。
- 漏洞按 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 私下报告，不使用公开 issue 或 PR。

# Contributing to Muse Console

English is canonical. [简体中文](CONTRIBUTING.zh-CN.md)

## Setup and checks

Work from the Muse Console repository root (`https://github.com/YunMengXue42/muse-console`). Repository access/public availability is not assumed. The distribution/CLI is `muse-console`, the Python package is `muse_console`, and packaged UI assets live in `src/muse_console/web`.

Use **uv 0.12.11**, **Python 3.12** for local development, and **Node.js 24** as in CI. The declared Python minimum is 3.10; CI defines a 3.10/3.12 matrix on Windows, Linux x64 and Linux ARM. A workflow definition is not evidence that a run passed.

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

Run checks with `MUSE_CONSOLE_DRIVER=mock` and `MUSE_CONSOLE_KEEPALIVE_ENABLED=false` in a clean, isolated environment, not against real accounts. CI runs `uv run --no-sync python -m pytest -q`; like the command above, it explicitly uses the selected environment's Python. Frontend `lint` checks TypeScript and Prettier; extension `build` includes TypeScript checking and has no separate lint/format script. Its formatter is the locked frontend Prettier installed by `npm ci` above. To fix formatting deliberately, use `uv run --no-sync ruff format .`, `npm run format` in `frontend`, or the extension formatter command with `--write` instead of `--check`, then review the diff and rerun checks.

`uv.lock` is a universal platform/Python lock, not a machine snapshot. Update dependencies deliberately with `uv lock`, review the diff and run `uv lock --check`; do not substitute `pip freeze`. Optional matting dependencies need separate platform/runtime acceptance.

If a preview holds the normal environment open, set `UV_PROJECT_ENVIRONMENT=.venv-hardening` before **all** sync/run commands (PowerShell: `$env:UV_PROJECT_ENVIRONMENT = '.venv-hardening'`). Do not reinstall into or stop an active preview environment. uv commands do not require manual virtualenv activation.

## Local development

Copy `.env.example` to a new local `.env` without overwriting existing settings (`cp .env.example .env` on POSIX; `Copy-Item .env.example .env` in PowerShell). Review it before starting:

```bash
uv run --no-sync muse-console
# Alternative:
uv run --no-sync python -m muse_console
```

Build the frontend first. The default `mock` driver needs no account. Use `MUSE_CONSOLE_`; core-managed legacy `MUSE2API_` aliases remain supported, with the new name taking precedence. For UI development run `npm run dev` in `frontend`; its API proxy defaults to `http://127.0.0.1:18610`.

See [Deployment](docs/DEPLOYMENT.md) before using real credentials, browser profiles, the extension or a reverse proxy. Extension build output is only for an explicitly authorized independent browser, never an invitation to inspect a daily browser profile.

Offline tools:

```bash
uv run --no-sync python tools/backup_restore.py --help
uv run --no-sync python tools/soak_check.py --help
```

Backup/restore requires all writers stopped and explicit disjoint paths. Soak monitoring is read-only health/readiness and optional redacted admin counts, not generation or renewal acceptance. Automatic renewal is off by default; its long-term effectiveness is unverified. Never point live tests at production data or a daily browser profile.

## Picking up a task

Tasks are listed in [TODO.md](TODO.md). When repository access permits, open an issue titled `[Claim] <ID> <task name>` and put your GitHub ID in its Owner column to avoid duplicate work. `TODO(contributors)` marks reserved extension points. Do not use a public task issue to report a vulnerability; follow [SECURITY.md](SECURITY.md).

## Conventions and drivers

- Dependency direction: `api → services → accounts/core → drivers → upstream`; never import upward.
- Muse page structure belongs in `drivers/browser/dom.py`; upstream URLs and cookie names belong in `upstream/muse.py` (paths relative to `src/muse_console`).
- Extend `DriverCapabilities` and the fake `MockDriver` when adding capabilities, so tests need no network access.
- Map upstream failures to `errors.py` types; do not silently swallow driver exceptions.
- CI must not contact real Muse. Use synthetic fixtures, not captured private credentials or personal conversations.

To add a driver, implement `MuseDriver` under `src/muse_console/drivers/<name>/`, register it in `drivers/registry.py` and `config.DriverName`, implement at least `chat_stream`, and leave unsupported capabilities raising `FeatureNotImplemented`. Add offline tests and documentation.

## Secrets and publication discipline

Never commit or publish `.env`, cookies, API/admin/client/device tokens, encryption/signing keys, any data directory, browser profiles, private conversations, media, backups or unredacted logs/screenshots. Use synthetic data and `example.com` or loopback in deployment examples. Check staged changes and delivery contents, not just `.gitignore`: ignored files and old Git history can still leak through archives or publication. If a secret escaped, revoke/rotate it and follow [SECURITY.md](SECURITY.md); deleting the visible file is not sufficient.

Run the offline publication guard from the repository root:

```bash
uv run --no-sync python tools/check_publication.py
uv run --no-sync python tools/check_publication.py --history
```

The default scans tracked working-tree files; `--history` scans blobs reachable from **all local Git refs**, not just the current branch, and reports commit-identity hashes for separate review. It is a limited pattern/path guard, not a credential-validity check or security audit. It excludes untracked/ignored files, unreachable objects, remote GitHub settings and external artifacts; a clean result is not publication clearance. Review those separately and see [Publishing](docs/PUBLISHING.md).

Do not upload private data to an issue, PR, CI artifact or external service. A local build/test is not authorization to publish a repository, package or image; publication and pushing require explicit approval.

## Packaging and upstream attribution

After the frontend build, a local wheel can be built with:

```bash
uv run --no-sync python -m pip wheel . --no-deps -w dist
```

This command requires pip in that interpreter; if unavailable, use a separate Python packaging environment with pip. Inspect the wheel for `muse_console/web/dashboard.html`, referenced assets, the CLI entry point and the MIT license notice. These are verification requirements, not a claim that a release passed them. No PyPI release is published; see [Deployment](docs/DEPLOYMENT.md) for container setup and availability.

Muse Console derives from [www222fff/muse2api](https://github.com/www222fff/muse2api). Preserve [LICENSE](LICENSE), including `Copyright (c) 2026 www222fff` and the MIT permission notice, in copies/substantial portions, packages and container images; retain the README upstream attribution. Renaming does not transfer copyright, imply upstream endorsement, or make upstream stars/releases/maintainers those of Muse Console. Keep all secrets, data, profiles, virtualenvs and `node_modules` out of delivery archives.

## Submitting changes

- Use branches such as `feat/<module>` or `fix/<issue>`.
- Run the relevant checks above and report actual results and unverified gates separately.
- Add a [CHANGELOG.md](CHANGELOG.md) entry for user-visible changes when appropriate.
- Keep English documentation canonical and update matching Chinese translations and reciprocal language links together.
- Report vulnerabilities privately using [SECURITY.md](SECURITY.md), not a public issue or PR.

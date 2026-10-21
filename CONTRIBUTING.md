# Contributing to Muse Console

## Setup

Clone `https://github.com/YunMengXue42/muse-console.git` and run these commands from that directory. The upstream attribution URL in README is not a clone URL for this project. The distribution/CLI is `muse-console`, the Python module/package is `muse_console`, and packaged UI assets live in `src/muse_console/web`.

```bash
python3 -m venv .venv && source .venv/bin/activate
uv sync --locked --extra dev
uv run --no-sync ruff format .
uv run --no-sync ruff check .
uv run --no-sync pytest
cd frontend
npm ci
npm run format
npm run lint
npm test
npm run build
```

Start with `muse-console` or `python -m muse_console` after building the frontend. Copy `.env.example` to `.env` for local settings. Prefer `MUSE_CONSOLE_`; legacy `MUSE2API_` aliases are core-managed, with the new name taking precedence.

The default is `MUSE_CONSOLE_DRIVER=mock`, so you can work on the API layer and front end without any account.

Use uv 0.12.11 as in CI. `uv.lock` is a universal platform/Python lock, not a snapshot of one machine. Update dependencies deliberately with `uv lock`, review the resulting diff and run `uv lock --check`; do not replace it with `pip freeze`. CI covers Python 3.10/3.12 on Windows, Linux x64 and Linux ARM. Optional matting dependencies require their own platform/runtime acceptance.

For QA while a preview holds the normal virtualenv open, set `UV_PROJECT_ENVIRONMENT=.venv-hardening` before `uv sync --locked --extra dev --python 3.12` and all `uv run --no-sync ...` commands. Do not reinstall into or stop an active preview environment. On PowerShell use `$env:UV_PROJECT_ENVIRONMENT = '.venv-hardening'`.

Extension checks run from `extension`: `npm ci`, `npm test`, `npm run build` (the build includes TypeScript checking). After installing frontend dependencies, formatting is checked with `node ../frontend/node_modules/prettier/bin/prettier.cjs --check "src/**/*.ts" "tests/**/*.ts" "*.json" "*.mjs" "*.html" "*.css"`. CI uses this locked formatter and explicit TypeScript checking, not absent optional scripts. Build output is for an explicitly authorized independent browser only.

Offline operations: `python tools/backup_restore.py --help` and `python tools/soak_check.py --help`. Backup/restore requires all writers stopped and explicit disjoint paths. Soak checks health/readiness and optional redacted admin counts; it is not model or renewal acceptance. Never point live tests at a daily browser profile or production data.

## Picking up a task

Available tasks are listed in [TODO.md](TODO.md). Before starting, open an issue titled `[Claim] <ID> <task name>` and put your GitHub ID in the "Owner" column of TODO.md, so that nobody duplicates the work. Places marked `TODO(contributors)` in the code are the reserved extension points.

## Conventions

- Dependency direction: `api → services → accounts/core → drivers → upstream`. Never import upward.
- Anything that depends on muse.ai's page structure belongs only in `drivers/browser/dom.py`; upstream URLs and cookie names belong only in `upstream/muse.py`.
- When adding a driver capability, update `DriverCapabilities` and add a fake implementation to `MockDriver` so that tests never need network access.
- Upstream errors must be mapped to the types in `errors.py`; never swallow exceptions inside a driver.
- CI must never contact the real muse.ai; tests that need upstream data should use recorded fixtures.
- Never commit cookies, the `data/` directory or `.env`.

## Adding a driver

1. Implement a `MuseDriver` subclass under `src/muse_console/drivers/<name>/`.
2. Register it in `drivers/registry.py` and add its name to `config.DriverName`.
3. Implement at least `chat_stream`; leave other capabilities raising `FeatureNotImplemented` until they are done.
4. Add tests and documentation.

## Packaging

After the frontend build, create a wheel with `python -m pip wheel . --no-deps -w dist` and inspect that it contains `muse_console/web/dashboard.html` and its referenced assets. Verify the wheel also contains the MIT license notice with `Copyright (c) 2026 www222fff`; preserve [LICENSE](LICENSE) and the README upstream attribution in redistributions. These are verification requirements, not claims that a release has passed them. Use local-source builds; no published PyPI or registry identity is assumed. Keep `.env`, all data directories, encryption keys, browser profiles, virtualenvs and `node_modules` out of delivery archives.

For local UI development, run the backend normally and `npm run dev` from `frontend`; its API proxy defaults to `http://127.0.0.1:18610`. On Windows, activate with `.venv\\Scripts\\activate` (PowerShell: `.venv\\Scripts\\Activate.ps1`) instead of the POSIX `source` command.

## Submitting changes

- Branch names: `feat/<module>`, `fix/<issue>`.
- Make sure `pytest` and `ruff check .` pass before opening a PR.
- Add a line to [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- Report security problems privately as described in [SECURITY.md](SECURITY.md).

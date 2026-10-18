# Contributing

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
ruff format .
ruff check .
pytest
cd frontend
npm ci
npm run format
npm run lint
npm test
npm run build
```

The default is `MUSE2API_DRIVER=mock`, so you can work on the API layer and front end without any account.

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

1. Implement a `MuseDriver` subclass under `src/muse2api/drivers/<name>/`.
2. Register it in `drivers/registry.py` and add its name to `config.DriverName`.
3. Implement at least `chat_stream`; leave other capabilities raising `FeatureNotImplemented` until they are done.
4. Add tests and documentation.

## Packaging

After the frontend build, create a wheel with `python -m pip wheel . --no-deps -w dist` and inspect that it contains `muse2api/web/dashboard.html` and its referenced assets. Keep `.env`, all data directories, encryption keys, browser profiles, virtualenvs and `node_modules` out of delivery archives.

For local UI development, run the backend normally and `npm run dev` from `frontend`; its API proxy defaults to `http://127.0.0.1:18610`. On Windows, activate with `.venv\\Scripts\\activate` (PowerShell: `.venv\\Scripts\\Activate.ps1`) instead of the POSIX `source` command.

## Submitting changes

- Branch names: `feat/<module>`, `fix/<issue>`.
- Make sure `pytest` and `ruff check .` pass before opening a PR.

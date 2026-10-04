<h1 align="center">muse2api</h1>

<p align="center">
  An async gateway that exposes the <a href="https://muse.ai">muse.ai</a> web app as an <b>OpenAI-compatible API</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/API-OpenAI%20Compatible-green" alt="OpenAI Compatible" />
  <img src="https://img.shields.io/badge/status-v0.1%20framework-orange" alt="Status" />
  <img src="https://img.shields.io/badge/License-MIT-lightgrey" alt="License" />
</p>

---

## Contents

- [Overview](#overview)
- [Status](#status)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Quick start](#quick-start)
- [API examples](#api-examples)
- [Configuration](#configuration)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)

## Overview

muse2api turns muse.ai's chat, text-to-image and text-to-video features into standard OpenAI endpoints (`/v1/chat/completions`, `/v1/images/generations`, ...). Existing OpenAI SDKs and clients such as ChatGPT-Next-Web, LobeChat or Cherry Studio work by just changing their `base_url`.

The project focuses on **clean engineering and easy collaboration**:

- **Pluggable drivers**: how we reach upstream is abstracted behind a `MuseDriver` interface. There are currently a mock driver and a browser (CDP) driver, plus a reserved slot for a direct-protocol (HTTP) driver. They are fully independent of each other.
- **Strict layering**: the protocol layer, service layer, account pool and drivers each have a single responsibility and can be developed and tested on their own.
- **Fully async**: built on FastAPI + asyncio; the CDP client uses `websockets`, so no request ever blocks a thread.
- **Account pool**: multiple scheduling strategies, per-account concurrency limits, cooldown after failures, automatic removal of expired sessions, and automatic failover to another account.
- **Offline development**: the mock driver needs no account and makes no network calls, so front-end and protocol work can move forward independently.

## Status

> **v0.1, framework stage**: the architecture and basic features are fully written, but the test suite has not been run yet and nothing has been tested against a real muse.ai account.

| Module | Status | Notes |
|---|---|---|
| OpenAI protocol layer (chat streaming/non-streaming, images, videos, models) | ✅ Implemented | Multi-turn chat, system prompts, image input, model aliases |
| Account pool (scheduling / cooldown / invalidation / failover) | ✅ Implemented | `lru`, `round_robin` and `affinity` strategies |
| Background tasks (video) | ✅ Implemented | Create a task, then poll for the result; unfinished tasks are marked interrupted after a restart |
| Admin API (account CRUD, renewal, reset, status) | ✅ Implemented | |
| Mock driver | ✅ Implemented | Offline fake data |
| Browser driver (Chromium + CDP) | 🧪 Needs live testing | Full chat, image and video flows are written |
| Session renewal / keepalive | 🧪 Needs live testing | Plain HTTP, disabled by default |
| Direct HTTP protocol driver | 🚧 Reserved | Returns 501 |
| `/v1/responses`, `/v1/images/edits` | 🚧 Reserved | Returns 501 |
| Web console, cookie import extension, quota lookup | 🚧 Reserved | |

## Architecture

```
          OpenAI SDK / chat clients
                    │  HTTP (Bearer key)
┌───────────────────▼────────────────────────────────────┐
│ api/        Protocol: routes · validation · auth · SSE │
├────────────────────────────────────────────────────────┤
│ services/   Gateway (failover) · TaskManager           │
├──────────────────────────┬─────────────────────────────┤
│ accounts/  Account pool   │ core/  Model aliases        │
│ strategies · cooldown ·   │        prompt conversion    │
│ renewal                   │        media storage        │
├──────────────────────────┴─────────────────────────────┤
│ drivers/    MuseDriver interface (only layer that      │
│             talks to upstream)                         │
│   ├─ mock      offline fake data                       │
│   ├─ browser   Chromium + CDP, one BrowserContext per  │
│   │            account                                 │
│   └─ http      direct protocol (reserved)              │
├────────────────────────────────────────────────────────┤
│ upstream/   muse.ai URLs, cookie names, session API    │
└────────────────────────────────────────────────────────┘
                    │
                 muse.ai
```

**How a chat request is handled**: the route resolves the model alias and merges the multi-turn `messages` into a single prompt, then hands it to the Gateway. The Gateway leases an account from the pool and asks the driver for a text stream. On failure, the account's state is updated according to the error type: accounts with an expired session are taken offline, accounts out of quota cool down, and other errors cause a short cooldown. As long as nothing has been sent to the client yet, the request is retried on another account.

For streaming requests, the first chunk is fetched before returning 200. Errors such as "no account available" or "upstream authentication failed" therefore come back with a proper HTTP status code instead of a stream that breaks halfway.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Project layout

```
muse2api/
├── src/muse2api/
│   ├── app.py                 # FastAPI app factory and lifespan
│   ├── config.py              # Settings (environment variables / .env)
│   ├── errors.py              # Error hierarchy → HTTP status + OpenAI error body
│   ├── api/
│   │   ├── deps.py            # Auth and dependency injection
│   │   ├── schemas.py         # OpenAI request models
│   │   └── routes/            # chat / images / videos / media / models / admin / responses
│   ├── services/
│   │   ├── gateway.py         # Runs driver calls on pooled accounts, retries on another account
│   │   ├── tasks.py           # Background long-running tasks
│   │   └── container.py       # Service wiring
│   ├── accounts/              # Account model, JSON store, pool, session keepalive
│   ├── core/                  # Model registry and aliases, prompt conversion, media storage
│   ├── drivers/
│   │   ├── base.py            # MuseDriver interface and data types
│   │   ├── mock.py
│   │   ├── browser/           # cdp.py / chromium.py / dom.py / driver.py
│   │   └── http/              # Reserved
│   └── upstream/muse.py       # Upstream constants and session renewal
├── tests/                     # Mock-driver tests, no network access
├── docs/ARCHITECTURE.md
├── TODO.md                    # Task board for contributors
├── Dockerfile · docker-compose.yml · .env.example
└── CONTRIBUTING.md
```

## Quick start

### Run locally

```bash
git clone https://github.com/www222fff/muse2api.git
cd muse2api
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env      # defaults to MUSE2API_DRIVER=mock, no account needed
python -m muse2api        # listens on http://127.0.0.1:18610 by default
```

If `MUSE2API_API_KEY` is not set, a key is generated on first start and saved to `data/api_key`.

### Docker

```bash
cp .env.example .env
docker compose up -d --build
```

### Use a real account (browser driver)

1. Install Chromium or Chrome and set `MUSE2API_DRIVER=browser` in `.env`.
2. Export the cookies from a browser that is logged in to muse.ai. At least `hatch_sess`, `hatch_gw`, `hatch_vml` and `hatch_native_auth_device` are required.
3. Import the account through the Admin API (see the example below).

## API examples

Every `/v1/*` endpoint requires the header `Authorization: Bearer <API_KEY>`. The `/admin/*` endpoints use `MUSE2API_ADMIN_KEY`, which defaults to the API key when unset.

**Chat (streaming)**

```bash
curl http://localhost:18610/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

**OpenAI Python SDK**

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:18610/v1", api_key="<API_KEY>")
resp = client.chat.completions.create(
    model="muse-chat",
    messages=[{"role": "user", "content": "Write a short poem about autumn"}],
)
print(resp.choices[0].message.content)
```

**Text to image**

```bash
curl http://localhost:18610/v1/images/generations \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"prompt":"A cyberpunk street on a rainy night","size":"16:9","response_format":"url"}'
```

**Text to video (async task)**

```bash
# Create a task; returns {"id": "task_xxx", "status": "queued", ...}
curl http://localhost:18610/v1/videos \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"prompt":"Maple leaves falling in a light breeze","duration":5,"size":"16:9"}'

# Poll the task; on success, result.url points to the video
curl http://localhost:18610/v1/videos/task_xxx -H "Authorization: Bearer $KEY"
```

**Import an account**

```bash
curl http://localhost:18610/admin/accounts \
  -H "Authorization: Bearer $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"label":"acc1","cookies":{"hatch_sess":"...","hatch_gw":"...","hatch_vml":"...","hatch_native_auth_device":"..."}}'
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` · `/readyz` | Liveness · readiness (driver state and available accounts) |
| GET | `/v1/models` | Model list, including aliases such as `gpt-4o` and `dall-e-3` |
| POST | `/v1/chat/completions` | Chat, streaming and non-streaming |
| POST | `/v1/images/generations` | Text to image, returns `url` or `b64_json` |
| POST | `/v1/videos` · GET `/v1/videos/{id}` | Create a video task · query a task |
| GET | `/v1/media/{name}` | Download generated media |
| GET/POST/PATCH/DELETE | `/admin/accounts[/{id}]` | Account CRUD |
| POST | `/admin/accounts/{id}/renew` · `/reset` | Renew session · reset account state |
| GET | `/admin/status` · `/admin/tasks` | Service status · task list |
| POST | `/v1/responses` · `/v1/images/edits` | Reserved, currently return 501 |

## Configuration

All settings come from environment variables (prefix `MUSE2API_`) or a `.env` file. See [.env.example](.env.example) for the full list. The most common ones:

| Variable | Default | Description |
|---|---|---|
| `MUSE2API_DRIVER` | `mock` | Driver: `mock` / `browser` / `http` |
| `MUSE2API_HOST` · `MUSE2API_PORT` | `127.0.0.1` · `18610` | Listen address and port |
| `MUSE2API_API_KEY` | auto-generated | Key for `/v1/*` endpoints |
| `MUSE2API_ADMIN_KEY` | same as API key | Key for `/admin/*` endpoints |
| `MUSE2API_PUBLIC_BASE` | empty | Public base URL used in media links; derived from the request when empty |
| `MUSE2API_POOL_STRATEGY` | `lru` | Account scheduling: `lru` / `round_robin` / `affinity` |
| `MUSE2API_MAX_FAILOVER` | `2` | Maximum number of retries on another account |
| `MUSE2API_CHROMIUM_PATH` | auto-detected | Path to the browser executable |
| `MUSE2API_KEEPALIVE_ENABLED` | `false` | Periodically renew sessions in the background |

## Roadmap

All reserved modules and outstanding work are tracked in **[TODO.md](TODO.md)**, in four groups:

- **Verification and live testing (do first)**: get the tests passing, test the browser driver against real accounts, and verify image/video generation and session renewal.
- **Drivers**: direct HTTP protocol driver, conversation thread reuse, quota lookup.
- **Protocol**: `/v1/responses`, `/v1/images/edits`, emulated tool calling, streaming heartbeats.
- **Accounts, admin and operations**: more cookie import formats, browser extension, web console, storage backends, media cleanup, multiple keys with rate limits, metrics.

Each task lists its location, approach and definition of done. Places marked `TODO(contributors)` in the code are the reserved extension points.

## Contributing

You are welcome to pick up any task in [TODO.md](TODO.md). Please open an issue describing what you plan to do before you start; see [CONTRIBUTING.md](CONTRIBUTING.md) for conventions.

```bash
pytest           # all tests use the mock driver
ruff check .     # lint
```

## Disclaimer

This project is for learning and technical research only. Please comply with muse.ai's terms of service and local laws. Never commit or publish account cookies.

## License

[MIT](LICENSE)

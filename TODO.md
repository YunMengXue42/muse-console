# Muse Console TODO

Paths below are relative to `src/muse_console/` unless they explicitly name a repository-root directory/file. The distribution/CLI is `muse-console`; module execution uses `python -m muse_console`. New configuration uses `MUSE_CONSOLE_`; core retains `MUSE2API_` aliases with new-name precedence.

This file lists every reserved module and all outstanding work. It is the single source of truth for task assignment.

**How to claim a task**: open an issue titled `[Claim] <ID> <task name>` (for example `[Claim] B1 Direct HTTP protocol driver`), put your GitHub ID in the "Owner" column below, and submit a PR, so that nobody duplicates the work. See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

Priorities: **P0** must be done first · **P1** core feature · **P2** better experience · **P3** nice to have

---

## Overview

| ID | Task | Priority | Location | Owner | Status |
|---|---|---|---|---|---|
| A1 | Install dependencies, get tests and lint passing | P0 | `tests/` | | Done locally in v0.3 |
| A2 | Test the browser driver with real accounts | P0 | `drivers/browser/` | | Previous single-account sample passed; hardening revision and multi-account load pending |
| A3 | Verify how image/video generation is triggered | P0 | `drivers/browser/driver.py` | | Previous image/video sample passed; bounded downloader live regression pending |
| A4 | Verify session renewal | P2 | `upstream/muse.py` | | Long-term unverified; v0.4.4 testing cancelled, not a release blocker |
| A5 | Verify the Docker image builds and runs | P1 | `Dockerfile` | | No-sandbox container design configured; native amd64/arm64 startup, persistence, permissions and registry acceptance pending |
| A6 | Enable GitHub Actions CI | P1 | `.github/workflows/ci.yml` | | Hosted CI passes for v0.4.2; publication changes need their own run |
| B1 | Direct HTTP protocol driver | P1 | `drivers/http/` | | Not started |
| B2 | Reuse conversation threads to cut first-token latency | P2 | `drivers/browser/` | | Done |
| B3 | Quota lookup | P2 | `MuseDriver.quota` | | Admin endpoint and mock done; browser driver pending |
| C1 | `/v1/responses` endpoint | P1 | `api/routes/responses.py` | | Done: streamed Responses events; official SDK run pending |
| C2 | `/v1/images/edits` endpoint | P1 | `api/routes/images.py` | | Done: multipart references, `mask` rejected |
| C3 | Emulated tool calling | P2 | `core/prompt.py` | | Not started |
| C4 | Streaming heartbeats to avoid client timeouts | P2 | `api/heartbeat.py` | | Done in 0.3.5: opt-in `MUSE_CONSOLE_STREAM_HEARTBEAT_INTERVAL` |
| D1 | Support more cookie import formats | P1 | `services/account_manager.py` | | Done: JSON, browser export, Netscape, header lines |
| D2 | Cookie import browser extension | P2 | `extension/` | | Implemented; previous independent-browser transfer/sync passed; HTTPS re-pair pending; long-term renewal unverified, excluded from v0.4.4 |
| D3 | Web admin console | P2 | `frontend/` / `web/` | | Implemented; v0.4.2 web-login dialog has rendered acceptance; upstream account renewal remains separate |
| D4 | SQLite / Redis storage backends | P3 | `accounts/store.py` | | Encrypted SQLite done; Redis out of scope |
| E1 | Automatic media cleanup | P2 | `core/media.py` | | Done: optional retention cleanup |
| E2 | Multiple API keys and rate limiting | P3 | `api/deps.py` | | Multiple keys and task/media ownership implemented; per-key quota/rate limiting pending |
| E3 | Observability (metrics, logs) | P3 | `core/metrics.py` | | Metrics done in 0.3.5 (`/admin/metrics`); per-request IDs pending |

---

## A. Verification and live testing

Earlier rebuild notes record local offline regressions, frontend checks and a mock-driver smoke test. These are not acceptance evidence for the renamed revision. Re-run offline checks for the exact revision; live Muse account acceptance and Docker runtime verification remain separate, unfinished gates.

### A1 Install dependencies, get tests and lint passing · P0
- Run `uv sync --locked --extra dev`, `uv run --no-sync ruff format .`, `uv run --no-sync ruff check .` and `uv run --no-sync python -m pytest`.
- Tests use fake upstreams; HTTP integration tests may bind ephemeral loopback listeners, never real Muse or production services.
- **Done when**: `pytest` and `ruff check .` both pass locally.

### A2 Test the browser driver with real accounts · P0
- Import cookies from a real muse.ai account, set `MUSE_CONSOLE_DRIVER=browser`, and verify each of the following:
  - the page reaches the "ready" state (`dom.PAGE_STATE`);
  - writing into the input box and clicking the send button work (`dom.fill_input`, `dom.CLICK_SEND`);
  - assistant replies are read correctly, with no duplicated or missing characters in the stream (`dom.CHAT_STATE`);
  - end-of-reply detection is accurate: it neither cuts replies short nor waits too long;
  - an expired cookie raises `UpstreamAuthError`, and running out of quota raises `UpstreamQuotaError`.
- Earlier real-page samples passed; recheck `drivers/browser/dom.py` for the exact hardening revision, particularly completion and bounded blob/media download.
- Verify that multi-account isolation via `Target.createBrowserContext` works.
- **Done when**: two or more accounts reliably complete streaming and non-streaming chats, and the findings are written up under `docs/`.

### A3 Verify how image/video generation is triggered · P0
- Generation is currently triggered by prefixing the prompt with "Generate an image / a video" (`BrowserDriver._media_prompt`). Confirm that muse.ai reliably generates media when asked this way.
- If the web app has a dedicated image/video entry point (a button, a mode switch, ...), use that instead.
- Confirm that aspect ratio (`16:9`, ...) and video duration are actually honoured.
- Confirm the attachment selector for results (`dom.ATTACHMENT`) and the download logic (`dom.fetch_as_base64`).
- **Done when**: text-to-image and text-to-video with a first frame both reliably produce files.

### A4 Verify session renewal · P2 (long-term; not a v0.4.4 blocker)
- v0.4.4 renewal testing is explicitly cancelled. Keep `MUSE_CONSOLE_KEEPALIVE_ENABLED=false`; the following future work needs separate authorization and remains unverified.
- Verify that calling `/api/session` really returns fresh cookies and extends the lifetime of `hatch_vml`.
- Verify the parameters and effect of `/api/hatch/vm/wake`.
- Determine a sensible renewal interval and update the default of `MUSE_CONSOLE_KEEPALIVE_INTERVAL`.
- **Done when**: with keepalive enabled, an account stays usable for three days or more.

### A5 Verify the Docker image builds and runs · P1
- Container design for 0.4.4: image defaults to `browser` and `CHROMIUM_NO_SANDBOX=true`; source retains `false`. Run non-root, drop ALL capabilities, set no-new-privileges, use init and loopback publishing. Never expose CDP, mount the Docker socket or use privileged mode. Browser vulnerabilities may expose container data; this is not equivalent to Chromium's sandbox.
- Acceptance requirement: record native amd64 and arm64 startup, restart/persistent-volume and permission checks using the documented hardening and 4 GiB / 512 PID budgets. Restart checks must verify graceful shutdown closes owned Chromium without leaving a profile lock; retain the existing `PROFILE_LOCKED` safety checks. A workflow definition or previous revision’s result is not acceptance for this revision.
- Release-image acceptance: verify package visibility (GHCR first publication defaults to private even for public repositories) and anonymous pulls of the exact release tag on both architectures; record publication status and results in the final release notes. Keep real-account login/chat/image/video verification separate from offline smoke checks. Renewal testing is excluded from v0.4.4.

### A6 Enable GitHub Actions CI · P1
- The repository already contains `.github/workflows/ci.yml` (lint and tests on Python 3.10 / 3.12).
- Pushing this file with a token requires the `workflow` scope; pushing over SSH does not.
- **Done when**: PRs run CI automatically and show the result.

---

## B. Drivers

### B1 Direct HTTP protocol driver · P1
Talk to muse.ai's backend directly, without a browser. This removes Chromium and significantly reduces latency and memory usage.
1. Capture and analyse the web app's traffic when sending a message (endpoints, handshake, framing, auth headers) and document it in `docs/protocol.md`.
2. Implement a transport client under `drivers/http/` that opens a session using the account's cookies.
3. Implement `chat_stream` by converting upstream incremental events into text deltas.
4. Implement image and video generation and attachment download.
5. Use recorded real responses as test fixtures; CI must not contact the real service.
- A more detailed step-by-step breakdown is in the docstring at the top of `drivers/http/driver.py`.
- **Done when**: chat works with `MUSE_CONSOLE_DRIVER=http` and passes the same API tests as the browser driver.

### B2 Reuse conversation threads to cut first-token latency · P2
Done. A follow-up whose `messages` continue the conversation already on the page stays on that tab and only the new user text is sent. Pass `user` to keep one person on one account (`affinity`, the default). A different history, a different `user`, a stuck page, or a long thread opens a fresh page.

### B3 Quota lookup · P2
- Implement `MuseDriver.quota` to read the account's plan, weekly usage and remaining quota.
- Show quota in `/admin/accounts`, and let scheduling prefer accounts with plenty of quota left.
- Also declare `quota=True` in `DriverCapabilities` and add a fake implementation to `MockDriver`.

---

## C. Protocol

### C1 `/v1/responses` endpoint · P1 · Done (SDK run pending)
- Support the OpenAI Responses API (used by default by newer clients such as Codex).
- Implemented in v0.3: `input` / `instructions` are flattened into `Gateway.chat_stream`, and streaming emits `response.created`, `response.output_text.delta`, `response.completed` and the surrounding item/content events.
- **Remaining**: run the official OpenAI SDK's `client.responses.create()` against a live account, streaming and non-streaming.

### C2 `/v1/images/edits` endpoint · P1 · Done (SDK run pending)
- Parse the multipart form (`image` / `image[]`, `prompt`, `size`, `response_format`).
- Implemented in v0.3: uploaded files become `ImageRequest.image` references and reuse the `generate_images` shaping. `mask` is rejected with `InvalidRequest` because muse.ai cannot inpaint.
- **Remaining**: run the official SDK's `client.images.edit()` against a live account.

### C3 Emulated tool calling · P2
- muse.ai has no native function calling. The idea is to describe the available tools and an output format in the prompt, parse tool calls out of the reply, and convert them to OpenAI's `tool_calls` format.
- muse.ai may refuse to follow the requested format, so verify feasibility first and ship it behind a config flag that is off by default.

### C4 Streaming heartbeats to avoid client timeouts · P2 · Done (0.3.5)
- Set `MUSE_CONSOLE_STREAM_HEARTBEAT_INTERVAL` (seconds, `0` = off) to answer 200 at once on `/v1/chat/completions` and `/v1/responses` streams and send `: keepalive` comments until the first token. Errors are then reported in-stream.
- Streaming requests wait for the first chunk before responding, which can take up to 45 seconds (`first_token_timeout`). Some clients and reverse proxies time out in the meantime.
- Approach: optionally return 200 immediately and send SSE comment lines (`: keepalive`) as heartbeats while waiting. The trade-off is that errors can then no longer be reported as HTTP status codes.

---

## D. Accounts and administration

### D1 Support more cookie import formats · P1
- `/admin/accounts` currently only accepts `{"cookies": {"name": "value"}}`.
- Add support for a raw `Cookie: a=1; b=2` request header string and for JSON arrays exported by extensions such as EditThisCookie (which include expiry times).
- Support importing multiple accounts in one request.

### D2 Cookie import browser extension · P2 · Implemented; HTTPS re-pair pending
- MV3 TypeScript extension automatically reads only Muse session cookies after normal user login, through the official API. Preserves HttpOnly, original lifetime, store and partition identity; never edits main-browser data.
- Uses one-use administrator pairing and a limited device token, not an admin key. Candidates go through `/credential-ingestions`, bounded owned-context validation, CAS versions and encrypted storage; no global CORS exception.
- Implemented quick pairing: manually copy/paste a JSON package containing origin, server ID, fingerprint, challenge and expiry. It does not enable synchronization automatically; target permission, identity verification and separate sync consent remain required. Verification reported: 59 frontend tests and 54 extension tests passed (including the named-identity consistency case). The locally rendered console generated pairing JSON successfully; extension verification covers DOM/offline tests only. Real-account end-to-end quick pairing/import has not been verified.
- Console supports pairing/list/revoke and capture-specific identity/version confirmation. Isolated login can optionally detect completion automatically; switches are off by default.
- Earlier independent-browser collection, pairing, transfer, automatic sync and post-import generation passed for one real Muse identity; duplicate records were merged. This is not proof of two-account or partitioned-cookie acceptance. Remaining: HTTPS re-pairing, live regression for this hardening revision, distinct-account/partition cases, ≤10-second import measurement and long-term renewal observation (cancelled for v0.4.4; not a release blocker). See [extension pairing](docs/DEPLOYMENT.md#6-extension-pairing-separate-exact-authorization) for setup and boundaries.

### D3 Web admin console · P2
- Create a `web/` directory, built on the existing `/admin/*` endpoints.
- Features: service status, account list (state / cooldown / errors / quota), importing and editing accounts, task list, media library, and an interactive API tester.
- Any tech stack is fine. Prefer a build output that FastAPI can serve as static files, so deployment gains no extra steps.

### D4 SQLite / Redis storage backends · P3
- Implement the `AccountStore` protocol in `accounts/store.py`, selectable through configuration.
- A Redis backend prepares for multi-instance deployments (the per-account concurrency counters would then also need to be shared across processes).

---

## E. Operations

### E1 Automatic media cleanup · P2 · Done
- `MUSE_CONSOLE_MEDIA_RETENTION_DAYS` enables mtime-based cleanup of generated `img_` and `vid_` files.
- Cleanup runs once at startup and hourly in the existing housekeeping loop; `0` keeps the previous no-cleanup behavior.
- Files that do not match the generated media naming contract are preserved.

### E2 Multiple API keys and rate limiting · P3
- Client keys already support create/revoke/rename and usage statistics; tasks and media are key-owned.
- Remaining: per-key rate/queue/usage quotas. Global process-local safety budgets are not per-key billing.

### E3 Observability · P3 · Metrics done (0.3.5)
- `/admin/metrics` (admin key) exposes `muse_chat_first_token_seconds`, `muse_generation_seconds`, `muse_upstream_requests_total` (per account/result), `muse_media_outcomes_total` (attachment_requested / retry_text_only / media_missing / timeout / succeeded…) and `muse_pool_accounts`. Process-local; resets on restart.
- Expose Prometheus metrics: request count, latency, first-token latency, per-account success/failure counts, queue wait time.
- Structured request logs with a unique ID per request for troubleshooting.

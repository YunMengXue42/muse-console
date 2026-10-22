# Muse Console

An independent, unofficial gateway for the [muse.ai](https://muse.ai) web app, with a **subset of OpenAI-style APIs** and a React administration console.

**English (primary)** | [简体中文](README.zh-CN.md)

Version **0.4.2** · Python **3.10+** · MIT

## Contents

- [Overview and status](#overview-and-status)
- [Quick start](#quick-start)
- [Real accounts and credential import](#real-accounts-and-credential-import)
- [API reference and examples](#api-reference-and-examples)
- [Compatibility boundaries](#compatibility-boundaries)
- [Security and operational boundaries](#security-and-operational-boundaries)
- [Configuration](#configuration)
- [Backup, upgrade and verification](#backup-upgrade-and-verification)
- [Architecture and development](#architecture-and-development)
- [Before publication](#before-publication)
- [License, attribution and trademarks](#license-attribution-and-trademarks)

## Overview and status

Muse Console provides text chat, reference-image generation, asynchronous image/video tasks, account scheduling and a management console. Clients can set a custom `/v1` base URL. Models such as `gpt-4o`, `claude-sonnet-4`, `deepseek-chat`, `dall-e-3` and `sora` are **routing aliases for Muse capabilities, not access to those vendors' models**. Prefer `muse-chat`, `muse-image` and `muse-video` in new integrations.

| Component | Implemented scope / status |
|---|---|
| Protocol | Chat ordinary/SSE output, stateless Responses subset, images, reference-image edits, video tasks, model listing |
| Account pool | Round-robin (default), LRU and affinity; per-account concurrency, cooldown, invalid-session exclusion and pre-output chat failover |
| Console | Account CRUD/merge, batch import preview, isolated login, client keys, request logs/statistics, task view and admin Prometheus metrics |
| Persistence | Encrypted SQLite account payloads; local task history, media ownership, signed download links and optional retention cleanup |
| Mock driver | Offline synthetic output without an account; mock video bytes are not playable video |
| Browser driver | Chromium + CDP implementation; prior live samples do not establish acceptance of this revision |
| Cookie extension | Read-only MV3 import, restricted device pairing and encrypted ingestion; requires explicit installation and permissions |
| Renewal / quota | Renewal disabled by default and not live-accepted; quota lookup is driver-dependent (unsupported drivers return 501) |
| Direct HTTP driver | Reserved; returns 501 |

Offline tests do not establish successful live authentication/generation, long-term renewal, SDK-wide compatibility, a Docker runtime pass or a successful GitHub Actions run. See [CHANGELOG.md](CHANGELOG.md) for changes and [TODO.md](TODO.md) for outstanding work. No PyPI release or Docker registry image is claimed; installation below uses source.

## Quick start

### Prerequisites

- Install **uv 0.12.11**, the version pinned in [CI](.github/workflows/ci.yml), using the [uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/) and select that version. Confirm with `uv --version`.
- Install **Python 3.12** with `uv python install 3.12`. The package supports Python **3.10+**; CI defines Python 3.10/3.12 jobs on Windows, Linux x64 and Linux ARM.
- Install **Node.js 24** (recommended for frontend/extension work, matching CI and the Docker build) and npm. A workflow definition is not evidence of a passed run.
- Git; Chromium/Chrome only for the real browser driver. Initial dependency installation/builds require network access.

### Offline mock service

Run from a fresh checkout; do not overwrite an existing `.env` or reuse private service data:

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

PowerShell: use `Copy-Item .env.example .env`, and run the frontend commands with `cd frontend` / `cd ..` instead of the subshell. uv commands need no virtualenv activation. Frontend builds write packaged assets to `src/muse_console/web`.

The example configuration selects `mock` and disables renewal. Mock generation does not contact Muse; caller-supplied remote image URLs can still trigger downloads, so use plain text/local fixtures for offline checks. Open `http://127.0.0.1:18610/dashboard`; `/admin` is an alias. If no API key is configured, first start saves a generated key in `data/api_key`. The admin key defaults to that key. Keep it private. Check `http://127.0.0.1:18610/healthz` and `/readyz` for liveness/readiness.

### Docker from source

```bash
cp .env.example .env
docker compose up -d --build
```

Compose builds the local tag `muse-console:local`, persists `./data` and publishes only `127.0.0.1:18610`. It is not a registry download. Use fresh test data for acceptance. Review [Deployment](docs/DEPLOYMENT.md) before enabling real accounts or remote access; this command is an instruction, not a claim of a completed build/run.

## Real accounts and credential import

### Isolated browser login or manual import

1. Install Chromium/Chrome, set `MUSE_CONSOLE_DRIVER=browser` and optionally `MUSE_CONSOLE_CHROMIUM_PATH`. Use only a project-owned profile, defaulting to `data/browser-profile`, never a daily browser profile. New/empty profiles are claimed with `.muse-console-owned-profile`; valid legacy `.muse2api-owned-profile` markers remain recognized. Nonempty unmarked profiles are refused. Do not copy or stamp a daily profile to bypass this check.
2. Authenticate to the dashboard with the admin key. Choose **网页登录添加**, interact with the displayed isolated page and complete passwords, MFA and CAPTCHA **manually**, then choose **完成验证并入池**. The server validates the upstream session before saving. Mock/HTTP drivers return 501 for browser login. Login controls never expose a CDP endpoint.
3. Alternatively choose **批量导入** for a JSON account array, browser cookie export, Netscape cookie file or one Cookie header per line. Preview checks structure/deduplicates `hatch_sess`; it does **not** prove a live login. `hatch_sess` is required; optional `hatch_gw`, `hatch_vml` and `hatch_native_auth_device` are forwarded when present.

Login contexts default to a 600-second TTL and at most 3 concurrent windows. `MUSE_CONSOLE_LOGIN_AUTO_COMPLETE_ENABLED=true` can complete validation/import after **user authentication**; it is not automated password or CAPTCHA solving.

### Optional Chrome / Edge extension

The extension reads the four Muse session cookies through the official cookies API (including HttpOnly and explicitly queried partitions). It does not copy profiles, alter/delete cookies, enable debugging on your daily browser or collect Google/Meta credentials.

1. Use an isolated browser-driver deployment. Enable `MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED` and set `MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN` to an exact trusted HTTPS origin, such as `https://console.example.com`, without a trailing slash. The explicit `MUSE_CONSOLE_CREDENTIAL_ALLOW_LOOPBACK_HTTP=true` exception permits loopback development only, not LAN/public HTTP.
2. Build with `cd extension`, `npm ci`, `npm test`, `npm run build`; load `extension/dist` yourself in Chrome/Edge's developer UI. Installation/permissions require your consent.
3. Generate a one-use challenge under **扩展设备配对** in the console. In extension options enter the same origin, grant permission, query identity and compare `server_id` and `fingerprint`. This fingerprint is an application checksum, **not TLS certificate pinning**. Exchange the challenge; never give the extension the admin key.
4. Separately consent to automatic synchronization and log in normally. The extension sends a store/partition snapshot after cookies settle; the backend probes `/api/session` in an owned temporary context before saving, without generation. Displays/receipts are redacted.
5. Devices default to adding one account. Cookie rotation is not stable identity proof: ambiguous identity or an advanced server version requires administrator confirmation of the **exact capture and target account**, then resubmission of the original candidate. This does not authorize future account switches; a changed version needs fresh confirmation. Lost candidates must be recaptured.
6. Pause/forget stops synchronization, not server account retention. Revoke the device in the console to stop future uploads. Device tokens are limited, expiring/rotatable grants: they cannot read cookies, list accounts or use admin APIs. Failed validation preserves existing credentials.

Extension secrets stay in worker memory/trusted session storage; its limited device token persists in trusted extension local storage. Server device tokens are hashed in `credential-devices.db`; account payloads are encrypted in `accounts.db`. Redacted receipts are limited to 30 days / 10,000 rows; source-binding/quota high-water marks remain durable. **Automatic session renewal, including 72-hour usability, remains unverified and disabled by default.** Scoped renewal uses owned browser contexts; legacy renewal uses HTTP. Neither guarantees indefinite validity or portability across devices/IPs.

## API reference and examples

Use `Authorization: Bearer <key>`. `/v1/*` accepts the admin key, the configured legacy API key or a non-revoked client key created in the console. `/admin/*` requires the admin key; ordinary client keys cannot administer the service. Media also supports expiring signed links as described below. API details remain here until a standalone reference exists.

Set `KEY` locally to your own key without posting it to logs or issues. These are Bash examples:

```bash
# Streaming chat; omit stream or set false for ordinary JSON.
curl http://127.0.0.1:18610/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-chat","messages":[{"role":"user","content":"Hello"}],"stream":true}'

# Stateless Responses subset.
curl http://127.0.0.1:18610/v1/responses \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-chat","input":"Write a short poem","store":false}'

# Image task: poll the returned id (omit async for synchronous output).
curl http://127.0.0.1:18610/v1/images/generations \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-image","prompt":"A red fox","size":"16:9","async":true}'
curl http://127.0.0.1:18610/v1/images/generations/task_example \
  -H "Authorization: Bearer $KEY"

# Reference-image generation, not masked inpainting.
curl http://127.0.0.1:18610/v1/images/edits \
  -H "Authorization: Bearer $KEY" \
  -F 'image[]=@reference.png' -F 'prompt=The same fox wearing a red scarf'

# Video task; JSON duration or matching seconds is also accepted.
curl http://127.0.0.1:18610/v1/videos \
  -H "Authorization: Bearer $KEY" \
  -F model=muse-video -F 'prompt=Maple leaves falling' -F seconds=5 \
  -F size=1280x720 -F first_frame=@reference.png
curl http://127.0.0.1:18610/v1/videos/task_example -H "Authorization: Bearer $KEY"
curl -L http://127.0.0.1:18610/v1/videos/task_example/content \
  -H "Authorization: Bearer $KEY" -o output.mp4
```

The optional `openai` SDK is a separate client dependency, not a server prerequisite:

```python
import os
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:18610/v1", api_key=os.environ["KEY"])
reply = client.chat.completions.create(
    model="muse-chat", messages=[{"role": "user", "content": "Hello"}]
)
print(reply.choices[0].message.content)
```

Image JSON accepts `image` as one reference or up to 4 data URLs, public HTTP(S) URLs or bare base64 strings. `response_format` is `url` or `b64_json`; asynchronous results use stored media URLs. For optional local transparent cutout, install with `uv sync --locked --extra dev --extra matting --python 3.12` and send `"background":"transparent"`. This uses local BiRefNet/rembg, not native upstream transparency; first use downloads model weights and CPU work can be substantial. Optional matting needs separate platform/runtime acceptance.

### Endpoint index

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz`, `/readyz` | Liveness / driver and account readiness |
| GET | `/v1/models` | Canonical models and aliases; unknown names return 404 `model_not_found` |
| POST | `/v1/chat/completions`, `/v1/responses` | Chat / stateless Responses subset |
| POST / GET | `/v1/images/generations`, `/v1/images/generations/{id}` | Image generation / task poll |
| POST | `/v1/images/edits` | Multipart `image`/`image[]` + `prompt`; no `mask` |
| POST / GET | `/v1/videos`, `/v1/videos/{id}`, `/v1/videos/{id}/content` | Create / poll / download video |
| GET | `/v1/media/{name}` | Owning-key/admin or signed-link download |
| GET | `/dashboard`, `/admin` | Console shell; admin APIs require authentication |
| GET/POST/PATCH/DELETE | `/admin/accounts[/{id}]` | Account management |
| POST | `/admin/accounts/{id}/renew`, `/reset`, `/merge` | Renew / reset / merge (`{"into":"acc_example"}`) |
| POST | `/admin/accounts/import` | `{content, preview}` batch import |
| GET/PATCH | `/admin/pool` | Pool status and persistent scheduling strategy |
| GET | `/admin/status`, `/admin/tasks`, `/admin/metrics` | Status / task list / Prometheus metrics |
| GET/POST/PATCH/DELETE | `/admin/keys[/{id}]` | Client key management; creation shows the secret once |
| GET | `/admin/requests`, `/admin/stats?window=1h\|24h\|7d` | Logs / aggregates; filters include key, account, status, path, since, hide_polls |
| POST/GET/DELETE | `/admin/login-sessions[/{id}]` | Create / inspect / cancel login |
| GET / POST | `/admin/login-sessions/{id}/frame`, `/input`, `/complete` | Screenshot / allowlisted input / validate-import |
| GET/POST/DELETE | `/admin/credential-devices`, `/pairings`, `/{id}` | Device list / pairing challenge / revoke |
| POST | `/admin/credential-devices/{id}/confirm` | Confirm `{capture_id, account_id}` |
| GET / POST | `/credential-ingestions/server`, `/pair`, `/rotate` | Identity / challenge exchange / device-token rotation |
| POST / GET | `/credential-ingestions`, `/{capture_id}` | Candidate import / own redacted receipt; restricted device token, 64 KiB limit |

Abbreviated suffixes in the table inherit the preceding route prefix. Task IDs and file names in examples are placeholders, not existing resources.

## Compatibility boundaries

| Surface | Supported | Not supported / not guaranteed |
|---|---|---|
| Chat | Full-history text flattened into a prompt; system/developer/user/assistant roles; user images; ordinary/SSE output; optional usage event | Tools/functions, audio/files, structured output, enforced token budgets, stop sequences or sampling controls |
| Responses | Text/image input, instructions, metadata echo, ordinary/SSE events, `store:false` | Stored responses, previous response/conversation references, tools, background execution, reasoning controls or JSON schema |
| Images | 1–4 sequential results, up to 4 references, URL/base64 output, optional local cutout | Mask inpainting, style/output-format controls or non-neutral quality |
| Video | Async task and one optional reference frame | Exact dimensions/duration, last-frame/video/audio references |
| Models | Muse capability IDs and compatibility aliases | The actual vendor models suggested by alias names |

Chat/Responses reject unknown controls or meaningful unsupported values with 400. Neutral defaults such as `temperature/top_p=1`, penalties `0`, `n=1`, empty tools, `tool_choice:auto/none`, text-only output and `store:false` are accepted without implying enforcement. Image `quality` accepts only `auto`/empty (or omission); empty `background` means unset. Schema failures may return validation errors rather than a protocol-control 400.

Video accepts JSON, multipart and form-encoded bodies. `duration`/`seconds` and `image`/`input_reference` must agree if supplied together. Multipart `first_frame` or the first `image[]` becomes the reference; `last_frame`, `video[]` and `audio[]` are rejected. `mode`, `resolution_name`, `generate_audio` and `watermark` are accepted but ignored. Sizes/durations are prompt hints, not guaranteed upstream parameters.

Usage is a text-length estimate, not exact tokens or upstream billing, and excludes image cost. SSE follows page updates and may arrive in large chunks. By default the first chunk is fetched before sending HTTP 200; optional `MUSE_CONSOLE_STREAM_HEARTBEAT_INTERVAL>0` sends 200 earlier, so later errors arrive in-stream. Official SDK acceptance for the implemented subset remains a separate verification gate.

## Security and operational boundaries

- **Keys:** set a separate `MUSE_CONSOLE_ADMIN_KEY` before sharing API access; otherwise the legacy API key is also an admin credential. Admin can use inference and inspect all tasks/media. Give each caller a distinct client key (`clientKey` in integrations), never the admin key. Stored client keys are shown once and retained as hashes/prefixes in `keys.json`.
- **Console/logs:** the console keeps the admin key in `sessionStorage` and clears it on logout. Request history in `requests.db` includes key/model/account/status/latency/client-IP metadata and poll flags; treat it as private. Forwarded IP headers are not an authorization boundary and require trusted proxy handling.
- **Conversation isolation:** reuse namespaces derive from authenticated key identity plus optional `user`; identical key/hint execution is serialized. Sharing a key shares that namespace. This does not erase upstream Muse memory: do not share a private-memory account among mutually untrusted users. Tenant-isolated upstream accounts and per-key billing/quotas are not provided.
- **Task/media ownership:** ordinary keys receive 404 for other owners' tasks/media; legacy ownerless records are admin-only. Signed media URLs default to one hour and act as shareable bearer capabilities. Revoking a stored client key invalidates its signed links; authorized task polling refreshes links. Media responses are `private, no-store`. Preserve `media-owners.db` and `media-signing.key` with the data.
- **Single worker:** run one process/worker per data directory. Scheduling, browser leases, runners and metrics are process-local; shared-directory multi-worker operation and distributed queues are unsupported. Restart marks unfinished tasks interrupted.
- **Execution:** media is not automatically replayed on another account after errors. A timeout/cancellation does not prove upstream stopped, did not charge, or refunded quota; inspect results before retrying. The optional same-account text-only video retry is not exactly-once execution. Multi-image partial recovery is not implemented.
- **Budgets:** defaults are 12 MiB request body, 16 in-flight generation HTTP requests, 32 queued/running tasks, 4 runners, 8 MiB per input image, 32 MiB per output, 1 GiB media storage and 16 generation tabs (900-second idle cleanup). Account concurrency defaults to 1. Generation/queue/storage exhaustion returns 429; oversized request bodies return 413. These are limits, not throughput promises. Full storage is not silently deleted; retention is opt-in.
- **Reference URLs:** public HTTP(S) raster-image MIME types only, standard ports, no URL credentials/fragments, private/link-local/metadata targets or unsafe redirects. DNS answers and every redirect are checked; connections pin an approved IP while retaining hostname TLS verification. This downloader ignores proxy environment variables and bounds transfer/decompressed size. MIME checking is not complete image-content validation; retain network egress controls.
- **Exposure:** keep admin and CDP private. Use trusted TLS and a protected backend; trust only your fixed reverse proxy for forwarded headers, never `forwarded_allow_ips="*"`. The ingestion origin check uses the resulting ASGI origin. No global CORS exception exists: if needed, allow exact client origins for `/v1/*` at the proxy, not wildcard origins or the dashboard. See [Deployment](docs/DEPLOYMENT.md).
- **Publication:** never include `.env`, cookies, keys, data directories, browser profiles, conversations/media, backups or unredacted logs/screenshots in Git, archives, issues or CI artifacts. Ignored files and old history still need review. Follow [SECURITY.md](SECURITY.md) for a suspected leak; deleting a file alone does not revoke a secret.

## Configuration

Use environment variables or `.env`; new deployments should use `MUSE_CONSOLE_`. Core-managed legacy `MUSE2API_` names remain accepted, with the new namespace taking precedence. See [.env.example](.env.example) and [config.py](src/muse_console/config.py) for all settings/ranges; deployment guidance is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

| Variable(s), prefixed `MUSE_CONSOLE_` | Default | Meaning |
|---|---|---|
| `DRIVER` | `mock` | `mock` / `browser` / reserved `http` |
| `HOST`, `PORT` | `127.0.0.1`, `18610` | Listen address/port |
| `DATA_DIR` | `data` | Private persistence root |
| `API_KEY`, `ADMIN_KEY` | generated, same as API key | Legacy inference key / administrator credential |
| `PUBLIC_BASE` | empty | Media base URL; otherwise derived from request |
| `REQUEST_LOG_RETENTION_DAYS`, `MEDIA_RETENTION_DAYS` | `14`, `0` | History retention / optional media cleanup (0 disables) |
| `MEDIA_URL_TTL` | `3600` | Signed URL lifetime in seconds |
| `MEDIA_MAX_BYTES`, `BROWSER_MEDIA_MAX_BYTES` | `33554432` each | Output / browser attachment byte caps |
| `MEDIA_STORAGE_MAX_BYTES`, `INPUT_IMAGE_MAX_BYTES` | `1073741824`, `8388608` | Total media / input image byte caps |
| `REQUEST_MAX_BYTES`, `REQUEST_MAX_INFLIGHT` | `12582912`, `16` | Body bytes / generation HTTP concurrency |
| `TASK_MAX_PENDING`, `TASK_MAX_CONCURRENT` | `32`, `4` | Queued+running tasks / runners |
| `BROWSER_MAX_TABS`, `BROWSER_IDLE_TTL` | `16`, `900` | Generation tabs / idle seconds |
| `POOL_STRATEGY`, `POOL_ACQUIRE_TIMEOUT` | `round_robin`, `60` | Scheduling / acquire deadline seconds |
| `ACCOUNT_MAX_CONCURRENCY`, `MAX_FAILOVER` | `1`, `2` | Per-account concurrency / pre-output chat failovers |
| `ACCOUNT_COOLDOWN` | `120` in code; `30` in example | Cooldown seconds; review the effective setting |
| `LOGIN_SESSION_TTL`, `LOGIN_SESSION_LIMIT` | `600`, `3` | Login seconds / context count |
| `LOGIN_AUTO_COMPLETE_ENABLED` | `false` | Validate/import after manual authentication |
| `CREDENTIAL_INGESTION_ENABLED`, `CREDENTIAL_SERVER_ORIGIN` | `false`, empty | Restricted import / exact receiver origin |
| `CREDENTIAL_ALLOW_LOOPBACK_HTTP` | `false` | Explicit loopback development exception |
| `CHROMIUM_PATH`, `BROWSER_PROFILE_DIR`, `BROWSER_PROXY` | detected, data-owned profile, empty | Browser executable / profile / browser-only proxy |
| `CHROMIUM_NO_SANDBOX` | `false` | Weaker isolation; prefer fixing sandbox support |
| `CHAT_TIMEOUT`, `IMAGE_TIMEOUT`, `VIDEO_TIMEOUT` | `300`, `240`, `600` | Generation timeouts in seconds |
| `FIRST_TOKEN_TIMEOUT`, `STREAM_HEARTBEAT_INTERVAL` | `45`, `0` | First output deadline / optional SSE heartbeat seconds |
| `VIDEO_DEADLINE_CAP`, `VIDEO_TEXT_ONLY_RETRIES` | `600`, `1` | Overall video budget / text-only resubmissions |
| `KEEPALIVE_ENABLED`, `KEEPALIVE_INTERVAL` | `false`, `21600` | Unverified renewal / interval seconds |
| `MATTING_MODEL` | `birefnet-general` | Optional local cutout model |

## Backup, upgrade and verification

Account payloads in `accounts.db` are encrypted using `credentials.key`: **back up the database and key together** while stopped. Losing the key makes accounts unrecoverable; encryption does not protect against access to both files. Legacy `accounts.json` is imported without modifying/deleting it and may still contain plaintext secrets. Restrict filesystem permissions (NTFS ACLs on Windows; `chmod` alone is insufficient there).

1. Stop **all writers**. Back up the complete data directory and matched source/image, including account/device databases, encryption key, media ownership/signing files and other state. Protect backups as secrets; never publish them.
2. The helper `uv run --no-sync python tools/backup_restore.py backup --data <stopped-data> --output <new-backup-dir> --stopped` creates an exclusive hash-verified copy. It does not stop services; existing targets, symlinks/reparse paths, hardlinks and SQLite sidecars are refused.
3. Test the new revision on a copy with a separate data directory/port. Check readiness, admin access, import preview, account distribution and an authorized real request before cutover. Never test on the only production copy.
4. Restore with `uv run --no-sync python tools/backup_restore.py restore --backup <backup-dir> --output <new-restore-dir> --stopped`. Roll back to the **matched old release and complete pre-upgrade backup**, not legacy JSON over newer SQLite. Hashes detect corruption, not malicious replacement.

Read-only observation: `uv run --no-sync python tools/soak_check.py --base-url http://127.0.0.1:18610 --duration 10 --interval 1 --report <new-report.jsonl>`. Remote URLs require HTTPS; optional `--admin-key-env <VARIABLE_NAME>` adds redacted counts. It follows no redirects and does not generate, renew or store response bodies. A 72-hour health report alone does not prove credential usability. Verify live behavior, Docker, optional matting and CI for the exact deployed revision separately.

## Architecture and development

Dependency direction: `api → services → accounts/core → drivers → upstream`. Routes handle authentication/validation/SSE; the gateway leases accounts and retries eligible chat failures only before output; drivers implement upstream access. Persistence uses bounded synchronous local I/O within an async application.

- `src/muse_console/`: app/config, protocol routes, account pool/storage, gateway/tasks, mock/browser/reserved HTTP drivers and packaged `web/` assets.
- `frontend/`: React/TypeScript console; `extension/`: optional MV3 importer.
- `tests/`: offline service, persistence and fake-CDP regressions; `tools/`: publication checks, backup/restore and monitoring.
- [Architecture](docs/ARCHITECTURE.md), [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), [Deployment](docs/DEPLOYMENT.md), [task board](TODO.md).

Run checks with mock/renewal disabled and synthetic fixtures, not private credentials:

```bash
uv lock --check
uv sync --locked --extra dev --python 3.12
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync python -m pytest
(cd frontend && npm ci && npm run lint && npm test && npm run build)
(cd extension && npm ci && npm test && npm run build)
```

Use `python -m pytest` rather than the direct pytest entry point. See CONTRIBUTING for extension formatting/type checks and isolated environments. Outstanding work includes exact-revision live regression, multi-account load/renewal acceptance, direct HTTP access, SDK subset acceptance and mask inpainting; no delivery dates or response SLA are promised.

## Before publication

Documentation preparation does **not** authorize changing repository visibility, pushing code or publishing packages/images. Follow the [publishing checklist](docs/PUBLISHING.md):

1. Review tracked changes, all local Git refs/history and commit identities for secrets or personal information. Check ignored/untracked files, delivery archives, build artifacts, screenshots and CI attachments separately; exclude real account details, production hosts/IPs and local absolute paths from examples.
2. Run the standard-library [publication guard](tools/check_publication.py) from the repository root:

   ```bash
   uv run --no-sync python tools/check_publication.py
   uv run --no-sync python tools/check_publication.py --history
   ```

   The default scans tracked working-tree files; `--history` scans blobs reachable from all local Git refs. JSON findings omit matched secret values and hash commit identities for separate review. Its finite pattern/path rules are not a security audit, credential-validity check or publication certification. Untracked/ignored files, unreachable objects, remote settings and external artifacts are outside its scope.
3. Revoke/rotate any exposed credential and review history/artifacts before release. Confirm actual test results, unresolved acceptance gates and retained MIT notices; a clean scan or CI guard alone is not clearance to publish.

## License, attribution and trademarks

Muse Console derives from the MIT-licensed [www222fff/muse2api](https://github.com/www222fff/muse2api). [LICENSE](LICENSE) retains `Copyright (c) 2026 www222fff` and the MIT permission notice. Copies/substantial portions, packages and container images must preserve these notices. Renaming does not transfer copyright or imply upstream endorsement; upstream stars, releases and maintainers are not those of Muse Console.

Maintained independently at [YunMengXue42/muse-console](https://github.com/YunMengXue42/muse-console). Distribution/CLI: `muse-console`; Python module: `muse_console`. Muse/muse.ai, OpenAI and other product/model names belong to their respective owners; names identify integrations/compatibility only and do not imply affiliation or endorsement. Comply with upstream terms, account authorization and applicable law. The MIT software is provided as-is, without a security, availability or compatibility guarantee.

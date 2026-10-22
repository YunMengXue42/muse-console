# API and configuration reference

[简体中文](API.zh-CN.md) · [README](../README.md)

## API reference and examples

Use `Authorization: Bearer <key>`. `/v1/*` accepts the admin key, the configured legacy API key or a non-revoked client key created in the console. `/admin/*` requires the admin key; ordinary client keys cannot administer the service. Media also supports expiring signed links as described below. 

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
- **Exposure:** keep admin and CDP private. Use trusted TLS and a protected backend; trust only your fixed reverse proxy for forwarded headers, never `forwarded_allow_ips="*"`. The ingestion origin check uses the resulting ASGI origin. No global CORS exception exists: if needed, allow exact client origins for `/v1/*` at the proxy, not wildcard origins or the dashboard. See [Deployment](../docs/DEPLOYMENT.md).
- **Publication:** never include `.env`, cookies, keys, data directories, browser profiles, conversations/media, backups or unredacted logs/screenshots in Git, archives, issues or CI artifacts. Ignored files and old history still need review. Follow [SECURITY.md](../SECURITY.md) for a suspected leak; deleting a file alone does not revoke a secret.

## Configuration

Use environment variables or `.env`; new deployments should use `MUSE_CONSOLE_`. Core-managed legacy `MUSE2API_` names remain accepted, with the new namespace taking precedence. See [.env.example](../.env.example) and [config.py](../src/muse_console/config.py) for all settings/ranges; deployment guidance is in [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

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


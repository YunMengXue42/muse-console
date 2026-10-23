# Deploying Muse Console

English is canonical. [简体中文](DEPLOYMENT.zh-CN.md)

These are deployment instructions and acceptance requirements, not a record of a completed deployment or security audit. Use an authorized checkout, isolated data and synthetic credentials first. No PyPI release is published.

## 1. Local mock baseline

Use Python 3.12, uv 0.12.11 and Node.js 24. From the repository root, copy `.env.example` to a **new** `.env` without overwriting existing configuration (PowerShell: `Copy-Item .env.example .env`). Review the file and process environment; `MUSE_CONSOLE_` takes precedence over legacy `MUSE2API_` names, and process environment overrides `.env` within a namespace.

```bash
uv sync --locked --python 3.12
cd frontend
npm ci
npm run build
cd ..
uv run --no-sync muse-console
```

For a fresh acceptance environment explicitly use:

```dotenv
MUSE_CONSOLE_HOST=127.0.0.1
MUSE_CONSOLE_PORT=18610
MUSE_CONSOLE_DRIVER=mock
MUSE_CONSOLE_DATA_DIR=data-mock
MUSE_CONSOLE_KEEPALIVE_ENABLED=false
MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED=false
MUSE_CONSOLE_LOGIN_AUTO_COMPLETE_ENABLED=false
```

Use `http://127.0.0.1:18610/healthz` for liveness, `/readyz` for readiness, and `/dashboard` (also `/admin`) for the console. Check `/admin/status` using the admin Bearer key. A mock result is synthetic and needs no upstream account; health/readiness do not prove real authentication or generation. Protect health endpoints at the proxy if operational metadata should not be public.

## 2. Container

The v0.4.4 GHCR image is not yet published; package visibility and anonymous pulls are not yet verified. GHCR packages default to private on first publication even for public repositories. Use source builds until release. The container target is `ghcr.io/yunmengxue42/muse-console` for linux/amd64 and linux/arm64 (`X.Y.Z`, `X.Y`, `latest`).

```bash
docker run -d --init --name muse-console -p 127.0.0.1:18610:18610 --shm-size 1g --memory 4g --pids-limit 512 --cap-drop ALL --security-opt no-new-privileges -v muse-data:/app/data ghcr.io/yunmengxue42/muse-console:0.4.4
docker logs muse-console   # console URL and first-start key
```

Compose does the same: `docker compose up -d` pulls the image; `docker compose up -d --build` builds this checkout instead. The default tag is `0.4.4`; override deliberately with `MUSE_CONSOLE_TAG`.

- The image defaults to `MUSE_CONSOLE_DRIVER=browser` and `MUSE_CONSOLE_CHROMIUM_NO_SANDBOX=true`. Set `MUSE_CONSOLE_DRIVER=mock` for an offline trial.
- Runs as UID/GID 1000. `docker run` uses the `muse-data` volume; Compose normally prefixes its volume with the project name, so these commands do not automatically share data. Existing `./data` installations must set `MUSE_CONSOLE_DATA=./data` before upgrading; otherwise a new empty pool appears. No data is migrated automatically. Keep a stopped-writer backup and use the same mount to roll back. Host directories must be owned by UID 1000 with mode 0700; never use world-writable permissions.
- **Container design:** Chromium’s sandbox is disabled for compatibility. Keep the non-root user, `--init` (Compose `init: true`), `cap-drop ALL`, `no-new-privileges` and loopback publishing. Browser vulnerabilities may expose container data, including account credentials; these restrictions are not equivalent to Chromium’s sandbox or complete isolation. Never mount the Docker socket, use privileged mode, add `SYS_ADMIN` or use unconfined seccomp as a shortcut.
- The first start prints a generated key once and saves it to `/app/data/api_key`. Retained container logs still contain that key after restart; restrict log access and never upload unredacted logs. Set `MUSE_CONSOLE_ADMIN_KEY` to separate admin and client keys.
- Keep the loopback-only port mapping; put a TLS reverse proxy in front (section 4). Never publish CDP port 19210.
- `.env` next to `docker-compose.yml` is read if present. Settings under `environment:` win over it. `.env.example` is for source/mock use: copying it selects mock mode. Its commented `CHROMIUM_NO_SANDBOX=false` line preserves the source default `false` and image default `true`. Review all values rather than copying blindly.

## 3. One worker and a dedicated real browser

Run **one ASGI worker/process and one writer per data directory/profile**. Do not add workers, replicas sharing a volume, overlapping cron writers or reload processes. Pools, tasks, pairings, budgets and browser contexts have process-local state; SQLite is not distributed scheduling. Restart marks unfinished tasks interrupted rather than replaying them.

`mock` is offline; `browser` is the real Chromium/CDP implementation; `http` is reserved and returns 501 for unimplemented operations, not a production fallback. For an explicitly authorized real-account test:

1. Stop the mock instance. Choose a separate private data directory and a **new empty dedicated profile**. Set `MUSE_CONSOLE_DRIVER=browser` and, if needed, `MUSE_CONSOLE_CHROMIUM_PATH` to the installed Chromium/Chrome executable. Default profile: `data_dir/browser-profile`; override with `MUSE_CONSOLE_BROWSER_PROFILE_DIR` only to another authorized project-only directory.
2. Never use, copy, mark as owned, or open the files of the user's main/daily browser profile. First startup claims an empty directory with `.muse-console-owned-profile`; a valid legacy `.muse2api-owned-profile` is also recognized. Nonempty unmarked directories are refused. Use a new directory or an explicitly authorized migration, not a fabricated marker. Protected-root and symlink/junction/reparse checks are guards against mistakes, not an OS sandbox.
3. Run as a dedicated unprivileged OS identity with profile/data access restricted to it. For source deployments, keep Chromium's sandbox enabled (container exception: section 2) (`MUSE_CONSOLE_CHROMIUM_NO_SANDBOX=false`); fix sandbox/user-namespace support rather than running root or disabling it. The implementation also adds `--no-sandbox` when run as root, so the false setting does not make root safe.
4. Log in through the admin console's isolated manual browser session, completing authentication/MFA yourself, then finish verification/import. No password or CAPTCHA automation is promised. Batch cookie import previews structure but does not prove a live upstream session. Treat all imported files as secrets.
5. Validate login and each required chat/image/video operation separately using authorized accounts and budgets. A previous sample or an offline test is not live acceptance of this revision. Account-level upstream memory can cross conversations: do not share one upstream account among mutually untrusted tenants.

The service launches only its own Chromium and rejects an occupied CDP port instead of attaching to an unknown browser. Keep CDP loopback-only, firewall it, and never reverse-proxy, forward, tunnel or publish it.

## 4. TLS, proxy trust and CORS

Expose remote access only through trusted TLS, e.g. `https://example.com`. Keep the ASGI backend on loopback/a protected socket reachable only by the fixed reverse proxy. The proxy must enforce the intended Host, overwrite forwarded scheme/host information and strip client-supplied forwarding values. Configure Uvicorn to trust **only the fixed proxy address**, never `forwarded_allow_ips="*"`. The simple CLI does not expose these proxy options; use an explicitly configured Uvicorn factory invocation when needed, for example with a proxy on the same host:

```bash
uv run --no-sync uvicorn muse_console.app:create_app --factory --host 127.0.0.1 --port 18610 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

Configure the proxy to send `Host: example.com` and the correct `X-Forwarded-Proto`. Restrict backend reachability even on loopback where appropriate. Container proxy topology needs its own fixed trusted peer, not blindly this host-loopback example. Direct TLS is another Uvicorn option; do not disable certificate validation. Set `MUSE_CONSOLE_PUBLIC_BASE=https://example.com` for media links and, when ingestion is enabled, `MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN=https://example.com` (exact origin, no path/trailing slash). The ingestion layer compares the resulting ASGI origin, not arbitrary raw forwarding headers. Verify this behavior behind the actual proxy before importing credentials.

Keep the dashboard/admin same-origin. There is no global application CORS allowance. If a separate browser client calls `/v1/*`, implement proxy CORS for that route prefix only: allow each exact scheme/host/port origin, such as `https://example.com:8443`, answer preflights with only needed methods/headers (`authorization`, `content-type`, `accept`), add `Vary: Origin`, and retain headers on errors. Never use a global `*`, reflect arbitrary origins, expose admin routes, or treat CORS as authentication. The credential-ingestion routes have their own narrow extension-origin/preflight handling; do not replace it with a broad proxy rule. Verify allowed and rejected origins. For SSE, disable proxy response buffering and set bounded timeouts appropriate to generation.

## 5. Keys, storage and media

- **Master/legacy key**: `MUSE_CONSOLE_API_KEY`; when empty, startup generates and stores it in `data_dir/api_key`. Keep it server-side rather than handing it to every client.
- **Admin key**: set a separately generated `MUSE_CONSOLE_ADMIN_KEY`. If empty it falls back to the master key. Admin authentication is a superset and also works on API routes; this is not evidence of isolation when identical values are used. Use it only for management, never in client apps or the extension.
- **Client keys**: create separate revocable keys in the console for distinct clients/owners. These are for API access, not administration. Sharing a client key shares its owner/conversation namespace. Device tokens from pairing are a fourth, restricted credential type, not interchangeable with any of the above.

Generate independent high-entropy secrets locally, deliver them privately, keep `.env` out of Git and archives, and redact Authorization headers and query signatures in proxy/app diagnostics. TLS and restrictive OS permissions remain necessary even with encrypted accounts.

`accounts.db` encrypts complete account payloads using `credentials.key`; missing/wrong keys fail startup rather than replace data. This does **not** encrypt every file: profiles, generated media, request metadata, tasks, configuration and legacy `accounts.json` need protection. Legacy migration preserves the original plaintext JSON; handle its retention privately. Client keys are stored hashed, and `credential-devices.db` holds hashed device tokens and redacted receipts, but those databases still contain sensitive metadata. Use encrypted disks/backups where required, and service-account ownership/ACLs for the whole data tree and any external profile. Anyone holding both encrypted data and its key can decrypt it.

Media lives under `data_dir/media`. `media-owners.db` records key ownership, while `media-signing.key` signs expiring bearer URLs. Key-authenticated downloads require owner/admin authorization; signed URLs delegate temporary access to anyone possessing them. Revoking a stored client key invalidates its signed links, but expiration/ownership do not prevent copies already downloaded. Do not infer ownership for legacy files, serve the media directory statically, cache private responses at a proxy/CDN, or log URL signatures. Preserve private/no-store behavior. `MUSE_CONSOLE_MEDIA_URL_TTL` defaults to 3600 seconds; `MUSE_CONSOLE_MEDIA_RETENTION_DAYS=0` disables automatic deletion. Plan retention and disk cleanup explicitly.

## 6. Extension pairing: separate, exact authorization

Ingestion is off by default. Use an explicitly authorized **independent test browser**, never auto-install into or inspect the user's main browser. The extension requires Chrome/Edge-compatible MV3 (manifest minimum Chrome 132).

<a id="local-extension-import-with-docker"></a>

### Local extension import with Docker

The basic one-command start uses the real browser driver but leaves credential ingestion disabled; `/credential-ingestions/*` returns 404 while disabled. Use this **instead of the basic start command** to opt in for same-machine loopback HTTP only:

```bash
docker run -d --init --name muse-console -p 127.0.0.1:18610:18610 --shm-size 1g --memory 4g --pids-limit 512 --cap-drop ALL --security-opt no-new-privileges -e MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED=true -e MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN=http://127.0.0.1:18610 -e MUSE_CONSOLE_CREDENTIAL_ALLOW_LOOPBACK_HTTP=true -v muse-data:/app/data ghcr.io/yunmengxue42/muse-console:0.4.4
docker logs muse-console
```

If a container named `muse-console` already exists, stop and remove that container first (do not delete the volume), then recreate it with the same data volume. Never run two instances against the same data. For an existing Compose deployment, retain its actual project-prefixed volume name rather than blindly using `muse-data`. This command does not migrate data, pair a device or enable synchronization automatically.

Use the exact origin `http://127.0.0.1:18610` in both the console and extension (no path or trailing slash; `localhost` is a different origin). In the extension, explicitly allow loopback HTTP development mode, grant permission for that target, verify server identity and separately consent to synchronization. Server-side switches do not establish automatic trust; copying pairing JSON does not bypass these checks. Never use this HTTP exception for LAN or public access.

For remote deployments, configure trusted TLS as in section 4, set `MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN=https://example.com` and `MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED=true`, and retain `MUSE_CONSOLE_CREDENTIAL_ALLOW_LOOPBACK_HTTP=false`. Grant the extension only that same exact HTTPS origin.

### Pairing steps

1. On a separate browser-driver deployment set `MUSE_CONSOLE_CREDENTIAL_INGESTION_ENABLED=true` and the exact `MUSE_CONSOLE_CREDENTIAL_SERVER_ORIGIN=https://example.com`. Use trusted TLS. Only for local development may both sides explicitly allow exact loopback HTTP, e.g. `http://127.0.0.1:18610`, with `MUSE_CONSOLE_CREDENTIAL_ALLOW_LOOPBACK_HTTP=true` server-side. Remote/LAN plain HTTP is not allowed.
2. Build with `npm ci`, `npm test`, `npm run build` in `extension`. Manually load `extension/dist` only with the user's installation/permission consent. The manifest's optional HTTPS permission range is not blanket authorization to access other servers; grant only the selected target in extension options.
3. An admin generates a one-use pairing challenge in the console. Enter the same exact origin in extension options, grant that target permission, retrieve server identity and compare **both `server_id` and `fingerprint`** with the console before exchanging the challenge. The fingerprint is an application identity checksum, **not a TLS certificate pin**. Never enter the admin/master/client key into the extension. The implemented manually copied JSON quick-pair package carries the origin, server ID, fingerprint, challenge and expiry; it does not grant permissions or enable sync automatically. Treat it as a temporary secret and do not log/share it.
4. Separately enable synchronization with explicit acknowledgement that Muse session credentials will be sent to that server. Log in to Muse normally. Collection is read-only via the cookies API, limited to `hatch_sess`, `hatch_gw`, `hatch_vml`, `hatch_native_auth_device` and their original scope/store/partition. It does not copy profiles, change cookies or collect identity-provider passwords. The backend probes a fixed session endpoint in an owned temporary browser context before commit, not a generation request; successful import is not proof of generation.
5. A device's default new-account quota is one. Known upstream subjects may match an existing account, but cookie rotation alone does not establish identity. A missing/conflicting identity or newer server credential version can require admin confirmation of **one exact capture, target account and current credential version**. Verify the current account, confirm that capture and resubmit the original candidate. This is not blanket approval of future account switches; version changes require fresh confirmation, and lost candidates require recapture and reconfirmation.
6. Pause/forget stops local sync but does not delete server accounts. Revoke the device grant server-side to stop future uploads; device tokens expire and can rotate. They cannot read cookie values, list accounts or invoke admin APIs. Failed validation preserves existing credentials. Raw captures remain transient in trusted extension session storage; the restricted device token persists in trusted local storage. Protect the independent browser too.

## 7. Resource budgets and renewal gates

Start conservatively and measure CPU, RAM, disk, temporary storage, queue latency and upstream quota. Defaults include:

| Setting suffix (`MUSE_CONSOLE_`) | Default |
| --- | --- |
| `REQUEST_MAX_BYTES` / `REQUEST_MAX_INFLIGHT` | 12582912 bytes / 16 |
| `TASK_MAX_PENDING` / `TASK_MAX_CONCURRENT` | 32 queued+running / 4 running |
| `ACCOUNT_MAX_CONCURRENCY` / `BROWSER_MAX_TABS` | 1 per account / 16 generation tabs |
| `LOGIN_SESSION_LIMIT` / `LOGIN_SESSION_TTL` | 3 / 600 seconds |
| `INPUT_IMAGE_MAX_BYTES` | 8388608 bytes |
| `MEDIA_MAX_BYTES` / `BROWSER_MEDIA_MAX_BYTES` | 33554432 bytes each |
| `MEDIA_STORAGE_MAX_BYTES` | 1073741824 bytes |

Login and credential-validation contexts have separate bounds; the generation-tab cap is not a total Chromium memory limit. Add OS/container CPU/memory/process/disk and egress limits; Compose's shared-memory setting is not a RAM cap. Optional matting adds model downloads, storage and CPU/RAM requirements; validate separately. Public-only download validation is defense in depth, not a replacement for egress controls. Limits are process-local, not distributed rate billing or a capacity guarantee. Inspect failures before manually retrying media generation: exactly-once generation and upstream cancellation/refunds are not guaranteed. The examples use 4 GiB RAM and 512 PIDs as starting budgets; tune for measured workloads and host capacity, not as a claim of complete isolation.

`MUSE_CONSOLE_KEEPALIVE_ENABLED=false` remains the default. Its long-term effectiveness is unverified. Scoped credentials use owned-context browser renewal; legacy credentials retain an HTTP renewal path. Neither guarantees portability across devices/IPs or permanent login. `MUSE_CONSOLE_LOGIN_AUTO_COMPLETE_ENABLED=false` is a separate switch for finishing isolated manual login, not unattended password/MFA automation. Read-only `tools/soak_check.py` health monitoring is not renewal acceptance.

## 8. Consistent offline backup and complete restore

`tools/backup_restore.py` never stops a service, deletes files or overwrites destinations. **Stop all writers first**: application/container, owned Chromium, other instances, renewal/import jobs and maintenance scripts. Drain traffic; verify processes are stopped. `--stopped` is your acknowledgement, not an automatic lock or stop operation.

Back up the **whole stopped data directory**, not selected databases: include `accounts.db` with `credentials.key`, `credential-devices.db`, `media-owners.db` with `media-signing.key`, media, `api_key`, `keys.json`, tasks, pool/runtime policy, request history and any in-tree dedicated profile. Preserve external `.env`/secret configuration, external dedicated profiles and the deployed revision separately under equally strict protection, captured in the same stopped interval. Never include a main browser profile.

If SQLite `-wal`, `-shm` or `-journal` sidecars remain, the tool refuses. Do not delete them to force success: perform appropriate offline SQLite recovery/checkpointing for each database with all writers still stopped, verify integrity/clean closure, then retry. Symlink/reparse paths, hardlinks/special files and nonportable names are also refused; investigate rather than bypass checks.

From the repository root, using **nonexistent, mutually disjoint** output directories whose parent already exists:

```bash
uv run --no-sync python tools/backup_restore.py backup --data data --output ../muse-backup-001 --stopped
uv run --no-sync python tools/backup_restore.py restore --backup ../muse-backup-001 --output ../muse-restored-001 --stopped
```

Replace `data` with the actual stopped host data path (`./data` only if explicitly selected with `MUSE_CONSOLE_DATA=./data`; the default is a named volume, whose mounted contents must be handled with all writers stopped). Source and destination cannot be equal or nested; even an empty existing destination is refused. A failed operation retains partial output for private inspection. The tool creates restricted permissions (POSIX 0700/0600 or current-user Windows ACL), copies files and verifies inventory/SHA-256. Hashes detect corruption, **not malicious replacement** of manifest plus payload, and the tool does not encrypt backups. Encrypt and access-control backups separately.

Restore the entire verified snapshot into a new directory; never merge it into live data or restore only an account database without its keys, ownership and device state. While still stopped, restore matching external configuration/profile, assign the correct service/container ownership and ACLs (the backup operator may differ from UID 1000), then switch the data mount/path to the restored directory. Keep the previous directory private for rollback; never run both against the same profile/data. Start one worker in a restricted environment, check health/readiness, admin/client-key separation, account decryption, device revocation state and media ownership/signatures before reopening traffic. Use only an authorized test account for live checks; offline restores do not prove current upstream validity. A restore rehearsal and full live acceptance have not been performed by writing this guide.

## 9. Before any public distribution

Review staged files, full Git history and delivery archives for private data; `.gitignore` alone is not sanitization. Exclude all `.env` files, data variants, backups, profiles, keys, logs, private media, virtualenvs and `node_modules`. Rotate any exposed secret rather than relying on deletion. Preserve [LICENSE](../LICENSE), `Copyright (c) 2026 www222fff`, the MIT permission notice and README attribution to [www222fff/muse2api](https://github.com/www222fff/muse2api) in copies/substantial portions, packages and images. Upstream attribution does not imply endorsement. See [Contributing](../CONTRIBUTING.md) and [Security](../SECURITY.md), including the unverified private-reporting availability and its no-public-disclosure fallback. Publication, registry uploads and Git pushes require separate explicit authorization.

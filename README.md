# Muse Console

**Connect Muse to your AI tools. Manage accounts, keys and generation tasks in one console.**

English · [简体中文](README.zh-CN.md) · **0.4.2** · Python **3.10+** · **MIT**

Muse Console is an independent, unofficial gateway for [muse.ai](https://muse.ai), exposing chat, image and video generation through a subset of OpenAI-style APIs.

## How it works

```text
Chat clients / scripts / canvas tools
          ↓ OpenAI-style API
Muse Console: auth · account scheduling · async tasks · media access
          ↓ Dedicated Chromium browser
        muse.ai
```

Call the API from your tools and maintain the service through a web console. Clients do not need separate integrations for browser automation, account scheduling and upstream task polling.

## Why Muse Console

| Advantage | Implementation |
|---|---|
| **One integration** | One `/v1` base for chat, reference-image generation and video tasks, with streaming chat and a Responses subset |
| **Centralized accounts** | Browser login, batch import and an optional cookie extension; round-robin / LRU / affinity scheduling, per-account concurrency and cooldown |
| **Visible operations** | Account health, request statistics, task progress and error reasons in the console, plus Prometheus metrics |
| **Controlled access** | Separate client keys, task/media ownership checks, expiring download links and encrypted account payloads |
| **Developer-friendly** | Account-free mock mode and layered drivers keep UI, protocol and upstream work independent |

## Quick start

Install **uv 0.12.11, Python 3.12 and Node.js 24**, then run in a fresh directory:

```bash
git clone https://github.com/YunMengXue42/muse-console.git
cd muse-console
uv sync --locked --extra dev --python 3.12
(cd frontend && npm ci && npm run build)
cp .env.example .env
uv run --no-sync muse-console
```

Open `http://127.0.0.1:18610/dashboard`. The first generated key is saved in `data/api_key`.

The default **mock mode** needs no account. For real Muse access, install Chromium/Chrome, set `MUSE_CONSOLE_DRIVER=browser`, then log in or import accounts through the console. Passwords, MFA and CAPTCHA require manual completion.

In PowerShell, use `Copy-Item .env.example .env` and run the frontend commands separately. Do not overwrite existing configuration or reuse production data. [Deployment guide](docs/DEPLOYMENT.md)

## API example

Set your client's API base to `http://127.0.0.1:18610/v1` and use a client key created in the console.

```bash
curl http://127.0.0.1:18610/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-chat","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

Prefer `muse-chat`, `muse-image` and `muse-video`. See the [API reference](docs/API.md) for endpoints, parameters and more examples.

## Boundaries

- **A compatibility subset, not a full OpenAI implementation.** Model aliases do not provide those vendors' actual models. Tool calling, structured output and masked inpainting are unsupported.
- **One process per deployment.** Run one worker per data directory. Automatic renewal is off by default; long-term renewal and multi-account load still need acceptance. The direct HTTP driver is unfinished.
- **Protect real credentials.** Use a dedicated browser profile. Set a separate admin key before sharing access and create client keys for callers. Do not share upstream accounts containing private memories among mutually untrusted users.
- **Back up completely.** Stop all writers and back up the whole data directory, including both the account database and `credentials.key`. Use trusted TLS for remote access and keep CDP private.

Build from source; no published PyPI package or container image is provided. Real generation depends on upstream pages, account state and quota.

## Documentation

[API and configuration](docs/API.md) · [Deployment and backup](docs/DEPLOYMENT.md) · [Architecture](docs/ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

[Changelog](CHANGELOG.md) · [Roadmap](TODO.md) · [Publication checklist](docs/PUBLISHING.md)

## License and attribution

Derived from the MIT-licensed [www222fff/muse2api](https://github.com/www222fff/muse2api), independently maintained by [YunMengXue42](https://github.com/YunMengXue42/muse-console). Redistributions must preserve the original copyright and permission notice in [LICENSE](LICENSE).

This project is not affiliated with or endorsed by Muse, OpenAI or other model vendors. Follow upstream terms and account authorization. Software is provided as-is under the MIT license.

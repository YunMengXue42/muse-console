# Security Policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's private vulnerability reporting: **Security → Report a vulnerability** on this repository.

Include the affected version, steps to reproduce and the impact you observed. You should get a first reply within 7 days.

## Scope

In scope: authentication and key handling, admin/client key separation, media link signing and ownership, cookie encryption at rest, SSRF protection on reference image downloads, the browser extension pairing flow.

Out of scope: muse.ai itself, and deployments that ignore the documented settings (for example exposing the CDP port or running Chromium as root without sandbox).

## Handling secrets

Never paste real cookies, API keys, `.env` contents or data directories into issues, pull requests or logs. If you leaked one, rotate it first, then report.

# Security Policy

English is canonical. [简体中文](SECURITY.zh-CN.md)

## Supported versions

Security fixes target the latest maintained version. Older versions have no promised backports. This policy does not establish that any package/image is published, audited or production-approved.

## Reporting a vulnerability

- Only when **Security → Report a vulnerability** is available on this repository, use GitHub's private form: <https://github.com/YunMengXue42/muse-console/security/advisories/new>.
- Otherwise, **do not disclose vulnerability details in a public issue, PR or discussion**. Keep the report private and wait for maintainers to enable private reporting.
- No response-time SLA is promised.

Include the affected version/commit, a minimal synthetic reproduction, prerequisites, expected/observed behavior and impact. Redact hosts, identities, cookies, tokens, request data and screenshots. Never upload a real data directory or backup. Test only systems/accounts you own or are explicitly authorized to assess; avoid destructive testing and real upstream generation without separate approval.

## Scope and deployment boundaries

Relevant areas include authentication; master/admin/client key separation; media signing and ownership; encrypted account persistence; reference-download SSRF defenses; dedicated browser profile isolation; and extension pairing, capture-specific confirmation, revocation and credential import.

Muse's upstream service is independently operated; report upstream-only issues through its own channels. Misconfiguration is not evidence of a product defect, but report a reproducible bypass of a documented boundary privately. Never expose CDP, reuse a main browser profile or rely on a root/no-sandbox browser for isolation. See [Deployment](docs/DEPLOYMENT.md).

Offline mock/fake-CDP tests do not prove live authentication, generation, multi-account load, import latency or long-running renewal. Keepalive defaults off; 72-hour renewal remains unverified. Profile ownership markers are accident-prevention guards, not a filesystem sandbox. Sharing an upstream account is not a privacy boundary between untrusted tenants.

## Handling secrets and incidents

Never paste real cookies, API/admin/client/device keys, encryption/signing keys, `.env`, profiles, private prompts/media, data directories or backups into public reports, PRs, CI artifacts or logs. Use only synthetic examples and redacted diagnostics.

If exposure occurs, restrict access and revoke/rotate affected credentials first. Revoke extension device grants and upstream sessions as applicable; invalidate leaked signed links as needed. Encryption/signing key replacement requires a planned migration or recovery: do not delete keys and strand encrypted data. Preserve incident evidence privately, review repository history/artifacts/backups, and report through the private path above once available. Removing a file or rewriting history does not revoke a leaked secret. Do not publish the repository or push changes as part of incident handling without explicit authorization.

Muse Console derives from the MIT-licensed [www222fff/muse2api](https://github.com/www222fff/muse2api). Preserve [LICENSE](LICENSE), including `Copyright (c) 2026 www222fff` and its permission notice, when redistributing; this does not imply upstream security endorsement.

# Changelog

All notable changes to Muse Console are listed here. Versions follow [Semantic Versioning](https://semver.org/) while the project is pre-1.0: minor versions may change behavior.

## [0.3.7] - 2026-10-22

### Changed
- Console layout fits one screen: account pool, tasks and request log fill the window; only the table body scrolls, with sticky headers, filters and pagination always visible.
- Overview is a dense grid: metrics and live metrics in one block, shorter trend chart, breakdown cards scroll internally; upstream calls are grouped per account.
- Tighter page header, table rows and toolbars; account pool filters, search and strategy share one row. Mobile keeps the natural page flow.

## [0.3.6] - 2026-10-22

### Added
- Console: page, time window and filters are kept in the URL hash and restored after reload.
- Console: Prometheus metrics panel backed by `/admin/metrics`.
- Console: request log filters by path and API key.
- Console: per-account concurrency limit and account merge from the account pool.

### Changed
- Console dialogs moved into `ConsoleModals`; shared helpers in `console-utils`.

## [0.3.5] - 2026-10-22

### Added
- Optional streaming first-token heartbeat (`MUSE_CONSOLE_STREAM_HEARTBEAT_INTERVAL`, off by default).
- Prometheus metrics at `/admin/metrics`: first-token latency, per-account upstream results, media outcomes.

### Changed
- Media waiting logic extracted into the pure `MediaWait` state machine with full branch tests.

## [0.3.4] - 2026-10-22

### Added
- MV3 browser extension that reads Muse session cookies after a normal login and syncs them to a paired console.
- Console credential devices, trend charts and redesigned pages.
- `/v1/videos` accepts JSON and multipart; `video_url` on success; `/v1/videos/{id}/content` download.
- Automatic re-submission when a media reply comes back as text only.

### Changed
- Package renamed from `muse2api` to `muse_console` (CLI `muse-console`). `MUSE2API_` environment variables remain accepted as aliases.
- Encrypted SQLite account storage.
- Stale Chromium singleton locks are archived on startup instead of blocking the browser.

## [0.3.1] - 2026-10-19

### Added
- Modular rebuild: encrypted account pool, batch import, isolated manual browser login.
- React/TypeScript console replacing the inline single-page dashboard.
- Tool to extract Muse session cookies from a DevTools HAR file.
- CI for Python 3.10/3.12 on Linux x64, Linux ARM and Windows, plus extension, frontend and Docker smoke jobs.

## [0.2.0] - 2026-10-04 (upstream muse2api)

Dashboard, per-client API keys, request log, async image tasks, reference images and browser-driver reliability fixes.

## [0.1.0] - 2026-09-27 (upstream muse2api)

Initial layered OpenAI-compatible gateway with pluggable drivers.

[0.3.7]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.7
[0.3.6]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.6
[0.3.5]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.5
[0.3.4]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.4

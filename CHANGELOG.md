# Changelog

All notable changes to Muse Console are listed here. Versions follow [Semantic Versioning](https://semver.org/) while the project is pre-1.0: minor versions may change behavior.

## [0.4.3] - 2026-10-23

### Added
- Browser extension 0.2.0: icons, readable status in popup and options (headline badge plus per-session state instead of raw JSON), `npm run package` producing a reproducible zip with SHA-256, bilingual extension README.
- README screenshots from mock mode and a browser extension section.
- The extension zip is attached to GitHub releases (`muse-console-extension-<version>.zip` with SHA-256).

### Fixed
- Extension development mode no longer offers IPv6 loopback (`http://[::1]`): Chrome rejects it in extension CSP, so pairing could never connect. Use `127.0.0.1` or `localhost`.

## [0.4.2] - 2026-10-22

### Changed
- Web login dialog fits one screen: the live page keeps its aspect ratio on the left; session state (Chinese label, countdown that turns red in the last minute), three steps, text input, keys and actions sit in a right column. Narrow screens stack vertically.
- Mouse wheel over the live page scrolls the upstream page. Input and keys are disabled once the session expires.

## [0.4.1] - 2026-10-22

### Changed
- Account request failures use allowlisted Chinese reasons and a neutral, expandable history note instead of a persistent red warning. Current login invalidation has a separate reauthentication notice. Legacy records without detailed causes are labeled as unknown; raw upstream errors remain private.

## [0.4.0] - 2026-10-22

### Added
- Theme switch is a ceiling pull-cord (`pullcord`, MIT; brings in `motion`): pull the knob, click it or press Enter.
- Sidebar instance card shows the account split (available / busy / cooling / invalid) as a segmented bar with a legend, plus running and queued task counts.

### Changed
- Light theme background is a warm off-white (`#f4f4f2`).
- Overview fits one screen: metrics strip, trend beside the model breakdown, then account / key / status cards (plus process counters when metrics exist). Breakdown tables became bar lists with a soft blue/coral palette, staggered entry, growing bars and hover states.
- API keys page fits one screen: base URL and built-in identities share a row; the key table fills the rest with a sticky header.
- Instance card uses the Muse blue when online; the signal is a single soft halo instead of hard rings.
- Extension pairing dialog copy shortened; safety checks unchanged.

### Removed
- Sidebar theme and logout buttons. Closing the tab ends the session (the admin key lives in sessionStorage only).

## [0.3.10] - 2026-10-22

### Fixed
- Normalize CRLF and CR line endings to LF before filling and checking browser prompts, so Windows multiline prompts no longer loop until composer readiness times out. All other text and whitespace checks remain exact.
- Composer test fixtures now model native textarea newline normalization, with regression coverage for multiline prompts and content mismatch protection.

## [0.3.9] - 2026-10-22

### Changed
- Sidebar instance status trimmed to one line (state word + available/total accounts) and a thin capacity bar. Driver, version, hint and last sync moved to the hover tooltip and screen-reader text. Labels: 在线 / 无可用账号 / 驱动异常 / 连接断开.

## [0.3.8] - 2026-10-22

### Changed
- Sidebar instance status is a card: green when online, amber when no account is schedulable, red when the driver is down or the console API is unreachable.
- Animated signal (pulse rings online, breathing when warning, blinking when down), a flash on state change, a plain-language hint, schedulable-account bar and last-sync time. Respects reduced motion; compact on mobile.

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

[0.4.3]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.4.3
[0.4.2]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.4.2
[0.4.1]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.4.1
[0.4.0]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.4.0
[0.3.10]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.10
[0.3.9]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.9
[0.3.8]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.8
[0.3.7]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.7
[0.3.6]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.6
[0.3.5]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.5
[0.3.4]: https://github.com/YunMengXue42/muse-console/releases/tag/v0.3.4

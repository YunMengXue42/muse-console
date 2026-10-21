#!/usr/bin/env python3
"""Extract muse.ai session cookies from a Chromium/Chrome HAR export.

Why this exists: muse.ai stores its session in httpOnly cookies, so page scripts
cannot read them, and recent Chrome versions encrypt the cookie database with
App-Bound Encryption that cannot be exported from outside the browser. A DevTools
"HAR with content" export contains the Cookie request headers the browser really
sent, which is the only offline path that keeps every httpOnly value.

Usage:
    python tools/har_cookies.py muse.ai.har            # Cookie: header line
    python tools/har_cookies.py muse.ai.har --json     # {"hatch_sess": "...", ...}

The output can be pasted straight into the console "Import cookies" box, which
accepts JSON, Netscape, browser-export JSON and Cookie header lines.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SESSION_COOKIES = ("hatch_sess", "hatch_gw", "hatch_vml", "hatch_native_auth_device")
# hatch_gw is no longer issued by the site (observed 2026-10-20); only hatch_sess is required.
REQUIRED_COOKIES = ("hatch_sess",)
HOST_SUFFIX = "muse.ai"


def _cookie_pairs(header: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for chunk in header.split(";"):
        name, sep, value = chunk.strip().partition("=")
        if sep and name:
            pairs[name] = value
    return pairs


def extract(har: dict[str, object]) -> dict[str, str]:
    entries = har.get("log", {}).get("entries", [])  # type: ignore[union-attr]
    found: dict[str, str] = {}
    for entry in entries:
        request = entry.get("request", {})
        url = str(request.get("url", ""))
        if HOST_SUFFIX not in url:
            continue
        for header in request.get("headers", []):
            if str(header.get("name", "")).lower() != "cookie":
                continue
            found.update(_cookie_pairs(str(header.get("value", ""))))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", type=Path, help="HAR file exported from DevTools")
    parser.add_argument("--json", action="store_true", help="print a JSON object instead")
    parser.add_argument(
        "--all",
        action="store_true",
        help="keep every muse.ai cookie, not only the session ones",
    )
    args = parser.parse_args()

    try:
        har = json.loads(args.har.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read HAR: {exc}", file=sys.stderr)
        return 2

    cookies = extract(har)
    if not cookies:
        print("no muse.ai Cookie header found; reload the page with DevTools open", file=sys.stderr)
        return 1

    missing = [name for name in REQUIRED_COOKIES if not cookies.get(name)]
    if not args.all:
        cookies = {k: v for k, v in cookies.items() if k in SESSION_COOKIES}

    if args.json:
        print(json.dumps(cookies, ensure_ascii=False, indent=2))
    else:
        print("; ".join(f"{name}={value}" for name, value in sorted(cookies.items())))

    if missing:
        print(f"warning: missing session cookies: {', '.join(missing)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

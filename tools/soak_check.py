"""Foreground read-only HTTP monitoring; no generation, renewal, or response bodies in report.

Duration/interval are explicit seconds. Requests never follow redirects or use
proxy environment variables. Admin status is opt-in via an environment VARIABLE
NAME, never a key on the command line. Admin samples retain only whitelisted
nonnegative integer resource/account/task counts, not identities or raw bodies.
Missing tab counts on non-browser drivers remain absent, not invented zeros.
Exit 1 means a failed check or an enabled endpoint with zero samples. A pass
only describes observations in this window, not real model or renewal/72h proof.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base URL must be an explicit HTTP(S) origin without credentials")
    _ = parsed.port
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("remote monitoring requires HTTPS")
    return value.rstrip("/")


def admin_counts(data: dict) -> tuple[bool, dict]:
    """Whitelist numeric aggregates only; never return raw server fields."""
    driver = data.get("driver")
    if not isinstance(driver, dict):
        raise ValueError("missing driver health")
    resources = data.get("resources", driver.get("resources", driver))
    if not isinstance(resources, dict):
        raise ValueError("invalid resources")
    counts = {}
    for group, source, fields, required in (
        ("resources", resources, ("tabs", "busy_tabs"), False),
        ("accounts", data.get("accounts"), ("available", "invalid"), True),
        ("tasks", data.get("tasks"), ("pending", "running"), True),
    ):
        if not isinstance(source, dict):
            raise ValueError("invalid count group")
        selected = {}
        for field in fields:
            if field not in source and not required:
                continue  # Mock/non-browser drivers do not expose tab counts.
            value = source.get(field)
            if type(value) is not int or value < 0:
                raise ValueError("invalid count")
            selected[field] = value
        counts[group] = selected
    return driver.get("ok") is True, counts


def run(
    url: str,
    duration: float,
    interval: float,
    report: Path,
    *,
    admin_key_env: str | None = None,
    timeout: float = 5,
) -> bool:
    url = base_url(url)
    if not all(math.isfinite(v) and v > 0 for v in (duration, interval, timeout)):
        raise ValueError("duration, interval and timeout must be finite and positive")
    key = None
    if admin_key_env:
        key = os.environ.get(admin_key_env)
        if not key or any(c in key for c in "\r\n"):
            raise ValueError("admin key environment variable missing/invalid")
    # Report is exclusive-created before making any requests. Never logs URL,
    # environment variable name, headers, body, account identifiers or exceptions.
    start = time.monotonic()
    end = start + duration
    failed = False
    report = Path(report)
    with report.open("x", encoding="utf-8") as out:
        if os.name != "nt":
            os.fchmod(out.fileno(), 0o600)
        out.write(
            json.dumps(
                {"type": "start", "duration_seconds": duration, "interval_seconds": interval}
            )
            + "\n"
        )
        out.flush()
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            # TLS/client initialization is setup, not an observed monitoring window.
            start = time.monotonic()
            end = start + duration
            paths = ["/healthz", "/readyz"] + (["/admin/status"] if key else [])
            samples = dict.fromkeys(paths, 0)
            try:
                while time.monotonic() < end:
                    for path in paths:
                        remaining = end - time.monotonic()
                        if remaining <= 0:
                            break
                        began = time.monotonic()
                        status, ok, error = None, False, None
                        counts = {}
                        headers = (
                            {"Authorization": f"Bearer {key}"} if path == "/admin/status" else {}
                        )
                        try:
                            with client.stream(
                                "GET", url + path, headers=headers, timeout=min(timeout, remaining)
                            ) as response:
                                status = response.status_code
                                ok = status == 200
                                if ok:
                                    body = bytearray()
                                    for chunk in response.iter_bytes(chunk_size=8192):
                                        if (
                                            len(body) + len(chunk) > 65536
                                            or time.monotonic() >= end
                                        ):
                                            raise ValueError("response limit exceeded")
                                        body.extend(chunk)
                                    data = json.loads(body)
                                    if not isinstance(data, dict):
                                        raise ValueError("invalid status object")
                                    if path == "/admin/status":
                                        ok, counts = admin_counts(data)
                                    else:
                                        field = "ok" if path == "/healthz" else "ready"
                                        ok = data.get(field) is True
                                        driver = data.get("driver")
                                        if isinstance(driver, dict) and driver.get("ok") is False:
                                            ok = False
                        except (httpx.HTTPError, ValueError, RecursionError):
                            error = "request_or_response_failed"
                        # No raw error text: it may contain URLs, keys or server content.
                        ok = ok and error is None
                        failed |= not ok
                        samples[path] += 1
                        out.write(
                            json.dumps(
                                {
                                    "type": "check",
                                    "endpoint": path,
                                    "status": status,
                                    "ok": ok,
                                    "error": error,
                                    "counts": counts,
                                    "elapsed_seconds": round(time.monotonic() - start, 3),
                                    "latency_seconds": round(time.monotonic() - began, 3),
                                }
                            )
                            + "\n"
                        )
                        out.flush()
                    # Do not start a new cycle with only a fractional interval left.
                    # Finish the explicitly requested window without a near-zero timeout.
                    remaining = end - time.monotonic()
                    if remaining <= interval * 2:
                        if remaining > 0:
                            time.sleep(remaining)
                        break
                    time.sleep(interval)
            except KeyboardInterrupt:
                out.write(json.dumps({"type": "end", "interrupted": True, "passed": False}) + "\n")
                return False
            failed |= any(count == 0 for count in samples.values())
        out.write(
            json.dumps(
                {
                    "type": "end",
                    "interrupted": False,
                    "passed": not failed,
                    "samples": samples,
                    "observed_seconds": round(time.monotonic() - start, 3),
                }
            )
            + "\n"
        )
    return not failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--duration", required=True, type=float)
    parser.add_argument("--interval", required=True, type=float)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--admin-key-env")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    try:
        passed = run(
            args.base_url,
            args.duration,
            args.interval,
            args.report,
            admin_key_env=args.admin_key_env,
            timeout=args.timeout,
        )
    except (OSError, ValueError):
        parser.exit(2, "Invalid monitoring configuration or unavailable report path.\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

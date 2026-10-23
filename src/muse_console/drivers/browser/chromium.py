"""Chromium process management."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .profile_safety import (
    claim_or_verify_profile,
    clear_proven_stale_locks,
    validate_owned_paths,
)

log = logging.getLogger(__name__)

_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "/usr/bin/chromium",
    "/snap/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_chromium(configured: str = "") -> str:
    """find an executable without inspecting user browser profiles."""
    windows = tuple(
        str(Path(root) / suffix)
        for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        if (root := os.environ.get(name))
        for suffix in (
            "Google/Chrome/Application/chrome.exe",
            "Microsoft/Edge/Application/msedge.exe",
            "Chromium/Application/chrome.exe",
        )
    )
    for c in ((configured,) if configured else ()) + _CANDIDATES + windows:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    raise FileNotFoundError(
        "Chromium/Chrome not found; install it or set MUSE_CONSOLE_CHROMIUM_PATH"
    )


class ChromiumProcess:
    def __init__(
        self,
        executable: str,
        port: int,
        profile_dir: Path,
        headless: bool = True,
        proxy: str = "",
        no_sandbox: bool = False,
    ):
        self.executable = executable
        self.port = port
        self.profile_dir = profile_dir
        self.headless = headless
        self.proxy = proxy
        self.no_sandbox = no_sandbox
        self.proc: asyncio.subprocess.Process | None = None

    @property
    def http_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def exited(self) -> bool:
        """Whether the owned browser process is gone for good.

        On Windows chrome.exe is a launcher: it hands the command line to the
        real browser process and exits 0 immediately, so a clean exit proves
        nothing there. Only the DevTools endpoint is authoritative, and a
        non-zero exit is still a real failure on every platform.
        """
        if self.proc is None:
            return True
        if self.proc.returncode is None:
            return False
        return not (sys.platform == "win32" and self.proc.returncode == 0)

    async def browser_ws_url(self) -> str | None:
        if self.exited:
            return None
        try:
            # Local DevTools port must not follow http_proxy/https_proxy.
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                r = await client.get(f"{self.http_base}/json/version")
                return r.json().get("webSocketDebuggerUrl")
        except (httpx.HTTPError, ValueError):
            return None

    async def start(self) -> str:
        """launch only an owned process; reject occupied ports and locked profiles."""
        if self.proc is not None:
            raise RuntimeError("Chromium process is already owned by this manager")
        _, profile = validate_owned_paths(self.profile_dir, self.profile_dir)
        self.profile_dir = profile
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if sys.platform == "win32":
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # Reuse closed connections in TIME_WAIT, never a live listener.
                # bind alone with SO_REUSEADDR can overlap a wildcard binding;
                # listen must also succeed. Never enable SO_REUSEPORT.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                # Windows permits a specific bind alongside a wildcard listener;
                # an exclusive wildcard probe rejects both kinds of occupancy.
                host = "0.0.0.0" if sys.platform == "win32" else "127.0.0.1"
                probe.bind((host, self.port))
                if sys.platform != "win32":
                    probe.listen(1)
            except OSError as exc:
                raise RuntimeError("Chromium debugging port is already in use") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # A service stop kills the browser with its cgroup, so the singleton
        # symlinks stay behind; keep proving they are stale rather than refusing
        # every restart, and leave anything ambiguous to the caller.
        stale = clear_proven_stale_locks(self.profile_dir)
        if stale:
            log.warning("moved stale browser locks: %s", ", ".join(stale))
        claim_or_verify_profile(self.profile_dir)

        args = [
            self.executable,
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--autoplay-policy=no-user-gesture-required",
            "--window-size=1440,2200",
            "about:blank",
        ]
        if self.proxy:
            args.insert(1, f"--proxy-server={self.proxy}")
        if self.headless:
            args[1:1] = ["--headless=new", "--disable-gpu"]
        if self.no_sandbox or (hasattr(os, "geteuid") and os.geteuid() == 0):
            args.insert(1, "--no-sandbox")

        log.info("launching Chromium: %s", self.executable)
        # raw browser output can contain credentials, URLs and profile data.
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            for _ in range(60):
                if self.exited:
                    break
                if url := await self.browser_ws_url():
                    parsed = urlsplit(url)
                    if (
                        parsed.hostname not in ("127.0.0.1", "localhost")
                        or parsed.port != self.port
                    ):
                        raise RuntimeError("Chromium returned a non-local DevTools endpoint")
                    return url
                await asyncio.sleep(0.5)
            raise RuntimeError(
                "Chromium did not expose a DevTools endpoint" + self._failure_detail()
            )
        except BaseException:
            await self.stop()
            raise

    def _failure_detail(self) -> str:
        code = self.proc.returncode if self.proc else None
        return f" (exit code {code})" if code is not None else " (still running)"

    async def stop(self, browser=None) -> None:
        """Finish owned cleanup even if the caller is cancelled; never discover CDP.

        Budget: close command 1s, graceful exit 3s, terminate 2s, kill 1s.
        A Windows launcher exiting zero is not proof that the browser exited.
        """
        cleanup = asyncio.create_task(self._stop_owned(browser))
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop_owned(self, browser) -> None:
        proc = self.proc
        if proc is None:
            return
        if browser is not None and not browser.closed and not self.exited:
            try:
                await asyncio.wait_for(browser.send("Browser.close"), 1)
            except Exception:
                # Closing Chromium can disconnect CDP before replying. Never log
                # raw protocol exceptions; still give the owned process time.
                pass
            try:

                async def wait_exit():
                    if proc.returncode is None:
                        await proc.wait()
                    if sys.platform == "win32" and proc.returncode == 0:
                        # A launcher can exit during this wait, too. Only the
                        # existing CDP connection can observe browser shutdown.
                        # CDP exposes a closed property, not a disconnect event.
                        while not browser.closed:  # noqa: ASYNC110
                            await asyncio.sleep(0.05)

                await asyncio.wait_for(wait_exit(), 3)
            except asyncio.TimeoutError:
                pass
        if (
            sys.platform == "win32"
            and proc.returncode == 0
            and browser is not None
            and not browser.closed
        ):
            # The launcher has exited but the owned browser demonstrably has
            # not. Retain ownership for a later retry on this same connection.
            raise RuntimeError("browser shutdown incomplete (code=browser_close_timeout)") from None
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                # Keep ownership if even the killed process cannot be reaped.
                await asyncio.wait_for(proc.wait(), 1)
        self.proc = None

"""Scope-preserving session probe in an owned, ephemeral browser context."""

from __future__ import annotations

import asyncio
import json
import re
import sys

from ...accounts.credentials import CookieRecord, project_records, records_from_cdp, records_to_cdp
from ...upstream import muse
from .cdp import CDPSession


class SessionProbeError(Exception):
    def __init__(self, status: str, code: str):
        self.status, self.code = status, code
        super().__init__(code)


# ``/api/session`` returns the account's cloud workspace id. Two browser
# profiles of the SAME Muse account report the same vm_id even though every
# hatch_* cookie (including hatch_native_auth_device) differs, so this is the
# only upstream-issued value that can identify an account across logins.
_VM_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def account_subject(vm_id) -> str | None:
    """Stable account subject, or None when upstream reports no usable vm_id."""
    if isinstance(vm_id, str) and _VM_ID.match(vm_id):
        return "muse-vm:" + vm_id.lower()
    return None


def _process_gone(chromium) -> bool:
    """True when the owned browser process is gone for good.

    Windows chrome.exe detaches the real browser and exits 0 at once, so only
    the CDP session can prove liveness there; any other exit code is fatal.
    """
    exited = getattr(chromium, "exited", None)
    if exited is not None:
        return bool(exited)
    proc = getattr(chromium, "proc", None)
    if proc is None:
        return True
    if proc.returncode is None:
        return False
    return not (sys.platform == "win32" and proc.returncode == 0)


async def probe_session(
    driver, records: list[CookieRecord], *, connect=None, timeout: float = 20
) -> tuple[list[CookieRecord], str | None]:
    """Never use the default context, HTTP fallback, wake VM, or guess a subject.

    Caller must supply the project's started BrowserDriver, not an attached browser.
    Optional connect is for offline protocol tests only.
    """
    browser = getattr(driver, "_browser", None)
    chromium = getattr(driver, "_chromium", None)
    if (
        not browser
        or browser.closed
        or not chromium
        or _process_gone(chromium)
        or getattr(driver, "_credential_probe_unhealthy", False)
    ):
        raise SessionProbeError("retry_wait", "browser_unavailable")
    project_records(records)
    context = None

    async def probe():
        await browser.send(
            "Storage.setCookies",
            {"browserContextId": context, "cookies": records_to_cdp(records)},
            timeout=5,
        )
        target = await browser.send(
            "Target.createTarget", {"url": "about:blank", "browserContextId": context}, timeout=5
        )
        page = await (connect or CDPSession.connect)(
            f"ws://127.0.0.1:{chromium.port}/devtools/page/{target['targetId']}"
        )
        try:
            await page.send("Page.enable", timeout=5)
            await page.send("Page.navigate", {"url": muse.SITE}, timeout=5)
            for _ in range(40):
                location = await page.evaluate(
                    "({origin:location.origin,ready:document.readyState})", timeout=2
                )
                if (
                    isinstance(location, dict)
                    and location.get("origin") == muse.SITE
                    and location.get("ready") in ("interactive", "complete")
                ):
                    break
                await asyncio.sleep(0.1)
            else:
                raise SessionProbeError("awaiting_login", "muse_document_unavailable")
            result = await page.evaluate(
                "(async()=>{if(location.origin!=="
                + json.dumps(muse.SITE)
                + ")return null;const r=await fetch("
                + json.dumps(muse.SESSION_API)
                + ",{credentials:'include',redirect:'error',signal:AbortSignal.timeout(8000)});"
                "if(!r.ok)return {http:r.status};const b=await r.json();"
                "return {http:r.status,assigned:b.status==='assigned',"
                "vm_id:typeof b.vm_id==='string'?b.vm_id:null};})()",
                await_promise=True,
                timeout=10,
            )
            if not isinstance(result, dict):
                raise SessionProbeError("retry_wait", "session_probe_failed")
            if result.get("http") in (401, 403) or (
                result.get("http") == 200 and not result.get("assigned")
            ):
                raise SessionProbeError("awaiting_login", "session_not_assigned")
            if result.get("http") != 200 or result.get("assigned") is not True:
                raise SessionProbeError("retry_wait", "session_probe_failed")
            snapshot = await browser.send(
                "Storage.getCookies", {"browserContextId": context}, timeout=5
            )
            refreshed = records_from_cdp(snapshot.get("cookies", []))
            project_records(refreshed)
            partitions = {r.partitionKey for r in records if r.partitionKey}
            if any(r.partitionKey and r.partitionKey not in partitions for r in refreshed):
                raise SessionProbeError("rejected", "partition_mismatch")
            return refreshed, account_subject(result.get("vm_id"))
        finally:
            await asyncio.wait_for(page.close(), 5)

    try:
        allocation = asyncio.create_task(
            browser.send("Target.createBrowserContext", {"disposeOnDetach": True}, timeout=5)
        )
        cancelled = False
        while not allocation.done():
            try:
                await asyncio.shield(allocation)
            except asyncio.CancelledError:
                cancelled = True
        context = allocation.result()["browserContextId"]
        if cancelled:
            raise asyncio.CancelledError
        return await asyncio.wait_for(probe(), timeout)
    except (SessionProbeError, asyncio.CancelledError):
        raise
    except Exception:
        if context is None:
            # A timed-out allocation can have succeeded remotely without returning
            # its id. Stop further allocations until the owned driver restarts.
            driver._credential_probe_unhealthy = True
        raise SessionProbeError("retry_wait", "browser_validation_failed") from None
    finally:
        if context:
            task = asyncio.create_task(
                browser.send(
                    "Target.disposeBrowserContext", {"browserContextId": context}, timeout=5
                )
            )
            cancelled = False
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                task.result()
            except BaseException:
                driver._credential_probe_unhealthy = True
                raise SessionProbeError("retry_wait", "browser_cleanup_failed") from None
            if cancelled:
                raise asyncio.CancelledError

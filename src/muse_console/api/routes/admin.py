"""admin APIs for account operations, extension pairing, client keys and usage."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ... import __version__
from ...accounts.keepalive import renew_account
from ...accounts.model import Account, AccountStatus
from ...auth.keys import ApiKey
from ...errors import InvalidRequest, NotFound
from ...services.container import Services
from ...upstream.muse import missing_session_cookies
from ..deps import get_services, public_base, require_admin_key
from ..schemas import AccountCreate, AccountUpdate, KeyCreate, KeyUpdate

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


class AccountMerge(BaseModel):
    into: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def _get(svc: Services, account_id: str) -> Account:
    acc = svc.pool.get(account_id)
    if acc is None:
        raise NotFound(f"account '{account_id}' not found")
    return acc


@router.get("/status")
async def status(svc: Services = Depends(get_services)) -> dict:
    return {
        "version": __version__,
        "driver": await svc.driver.health(),
        "capabilities": svc.driver.capabilities.__dict__,
        "accounts": svc.pool.stats(),
        "tasks": svc.tasks.stats(),
    }


@router.get("/accounts")
async def list_accounts(svc: Services = Depends(get_services)) -> dict:
    return {"data": svc.pool.all_public()}


@router.post("/accounts")
async def create_account(body: AccountCreate, svc: Services = Depends(get_services)) -> dict:
    cookies = {k: v for k, v in body.cookies.items() if v}
    if not cookies:
        raise InvalidRequest("cookies must not be empty")
    try:
        acc = await svc.account_manager.create_legacy(body)
    except ValueError:
        raise InvalidRequest("this session already exists in the account pool") from None
    return {"account": acc.public(), "missing_cookies": missing_session_cookies(cookies)}


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: str, body: AccountUpdate, svc: Services = Depends(get_services)
) -> dict:
    _get(svc, account_id)
    changes = body.model_dump(exclude_unset=True)
    try:
        acc = await svc.account_manager.update(account_id, **changes)
    except ValueError:
        raise InvalidRequest("invalid account update or duplicate session") from None
    if body.cookies is not None:
        invalidate = getattr(svc.driver, "invalidate_account", None)
        if invalidate:
            await invalidate(account_id)
    return {"account": acc.public()}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, svc: Services = Depends(get_services)) -> dict:
    if not await svc.pool.remove(account_id):
        raise NotFound(f"account '{account_id}' not found")
    invalidate = getattr(svc.driver, "invalidate_account", None)
    if invalidate:
        await invalidate(account_id)
    return {"deleted": account_id}


@router.post("/accounts/{account_id}/merge")
async def merge_account(
    account_id: str, body: AccountMerge, svc: Services = Depends(get_services)
) -> dict:
    """Fold a duplicate entry into ``body.into``; the admin names both accounts."""
    _get(svc, account_id)
    _get(svc, body.into)
    try:
        acc = await svc.account_manager.merge(account_id, body.into)
    except ValueError:
        raise InvalidRequest("invalid merge request") from None
    devices = getattr(svc.credential_ingestion, "devices", None)
    rebound = devices.rebind(account_id, body.into) if devices is not None else 0
    invalidate = getattr(svc.driver, "invalidate_account", None)
    if invalidate:
        await invalidate(account_id)
    return {"merged": account_id, "account": acc.public(), "bindings": rebound}


@router.post("/accounts/{account_id}/reset")
async def reset_account(account_id: str, svc: Services = Depends(get_services)) -> dict:
    acc = _get(svc, account_id).model_copy(deep=True)
    acc.status, acc.cooldown_until, acc.last_error = AccountStatus.ACTIVE, 0.0, None
    await svc.pool.upsert(acc)
    return {"account": acc.public()}


@router.post("/accounts/{account_id}/renew")
async def renew(account_id: str, svc: Services = Depends(get_services)) -> dict:
    _get(svc, account_id)
    return await renew_account(svc.pool, svc.driver, account_id)


@router.get("/accounts/{account_id}/quota")
async def account_quota(account_id: str, svc: Services = Depends(get_services)) -> dict:
    """Ask the active driver for the account's quota snapshot and persist it in ``meta``.

    Drivers that do not implement ``MuseDriver.quota`` raise
    ``FeatureNotImplemented``, which the app renders as HTTP 501.
    """
    expected = _get(svc, account_id)
    acc = expected.model_copy(deep=True)
    snapshot = await svc.driver.quota(acc)
    acc.meta["quota"] = {"checked_at": time.time(), **snapshot}
    if not await svc.pool.upsert_if_current(acc, expected):
        raise InvalidRequest("account changed during quota lookup; retry the request")
    return {"account_id": acc.id, "quota": acc.meta["quota"]}


@router.get("/tasks")
async def list_tasks(
    request: Request, limit: int = 100, svc: Services = Depends(get_services)
) -> dict:
    views = []
    for task in svc.tasks.list(limit):
        view = task.model_dump(mode="json")
        view["result"] = svc.media.refresh_links(task.result, public_base(request), "admin")
        views.append(view)
    return {"data": views}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, svc: Services = Depends(get_services)) -> dict:
    return {"cancelled": await svc.tasks.cancel(task_id)}


# ---- client API keys ----
def _get_key(svc: Services, key_id: str) -> ApiKey:
    key = svc.keys.get(key_id)
    if key is None:
        raise NotFound(f"key '{key_id}' not found")
    return key


# Identities that authenticate without a stored key (see deps.require_api_key).
BUILTIN_KEYS = ("legacy", "admin")
_NO_USAGE = {"total": 0, "requests_24h": 0, "errors_24h": 0, "last_request_at": None}


def _usage(row: dict | None) -> dict:
    if row is None:
        return dict(_NO_USAGE)
    return {k: row[k] for k in _NO_USAGE}


@router.get("/keys")
async def list_keys(svc: Services = Depends(get_services)) -> dict:
    """Stored keys plus the built-in identities, each with request counts from the log."""
    usage = {r["key_id"]: r for r in (await svc.requests.key_usage())["data"]}
    return {
        "data": [{**k.public(), "usage": _usage(usage.get(k.id))} for k in svc.keys.all()],
        "builtin": [{"id": b, "name": b, "usage": _usage(usage.get(b))} for b in BUILTIN_KEYS],
    }


@router.get("/keys/usage")
async def key_usage(svc: Services = Depends(get_services)) -> dict:
    """Raw per-``key_id`` counts, including deleted keys still present in the log."""
    return await svc.requests.key_usage()


@router.post("/keys")
async def create_key(body: KeyCreate, svc: Services = Depends(get_services)) -> dict:
    key, plaintext = await svc.keys.create(body.name.strip(), body.note)
    # The only time the plaintext key is ever returned.
    return {"key": key.public(), "api_key": plaintext}


@router.patch("/keys/{key_id}")
async def update_key(key_id: str, body: KeyUpdate, svc: Services = Depends(get_services)) -> dict:
    key = _get_key(svc, key_id)
    if body.name is not None:
        key.name = body.name.strip()
    if body.note is not None:
        key.note = body.note
    if body.revoked is not None:
        key.revoked = body.revoked
    await svc.keys.save()
    return {"key": key.public()}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, svc: Services = Depends(get_services)) -> dict:
    if not await svc.keys.remove(key_id):
        raise NotFound(f"key '{key_id}' not found")
    return {"deleted": key_id}


# ---- request log ----
@router.get("/requests")
async def list_requests(
    key_id: str | None = None,
    account_id: str | None = None,
    status: Literal["2xx", "3xx", "4xx", "5xx", "failed"] | None = None,
    path: str | None = None,
    since: float | None = Query(default=None, description="Unix seconds"),
    hide_polls: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: Services = Depends(get_services),
) -> dict:
    return await svc.requests.query(
        key_id=key_id,
        account_id=account_id,
        status=status,
        path=path,
        since=since,
        hide_polls=hide_polls,
        limit=limit,
        offset=offset,
    )


@router.get("/stats")
async def stats(
    window: Literal["1h", "24h", "7d"] = "24h", svc: Services = Depends(get_services)
) -> dict:
    return {"now": time.time(), **await svc.requests.stats(window)}


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(svc: Services = Depends(get_services)) -> PlainTextResponse:
    """Prometheus text exposition; process-local, resets on restart."""
    from ...core import metrics

    pool = svc.pool.stats()
    gauges = [
        "# HELP muse_pool_accounts Accounts in the pool by state.",
        "# TYPE muse_pool_accounts gauge",
    ]
    for state, value in sorted(pool.items()):
        if isinstance(value, int | float) and not isinstance(value, bool):
            gauges.append(f'muse_pool_accounts{{state="{state}"}} {value}')
    body = metrics.render() + "\n".join(gauges) + "\n"
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

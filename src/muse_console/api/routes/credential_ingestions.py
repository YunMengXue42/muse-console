"""Separate device-only transport; no global CORS and no request-secret error echo."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...accounts.credentials import CredentialEnvelope
from ...services.credential_devices import CredentialBoundaryError
from ...services.credential_ingestion import configured_origin
from ..deps import get_services, require_admin_key

router = APIRouter()
LIMIT = 64 * 1024
EXTENSION_SCHEME = "chrome-extension://"
PREFLIGHT_METHODS = "GET, POST"
PREFLIGHT_HEADERS = "authorization, content-type"
PREFLIGHT_MAX_AGE = "600"


def extension_origin(value: str) -> str | None:
    """Return the extension origin when it is a well-formed MV3 extension id."""
    if not value.startswith(EXTENSION_SCHEME):
        return None
    suffix = value.removeprefix(EXTENSION_SCHEME)
    if len(suffix) == 32 and all(c in "abcdefghijklmnop" for c in suffix):
        return value
    return None


class PairInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    challenge: str = Field(min_length=1, max_length=128)
    server_id: str = Field(min_length=1, max_length=128)


class ChallengeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(default="", max_length=100)
    new_account_limit: int = Field(default=1, ge=0, le=10)


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    capture_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    account_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


def service(request: Request):
    ingestion = getattr(get_services(request), "credential_ingestion", None)
    if ingestion is None or not ingestion.enabled:
        raise CredentialBoundaryError("ingestion_disabled", 404)
    origin = configured_origin(ingestion.settings)
    # Never parse forwarding headers here. Direct TLS works with proxy_headers=False.
    # For TLS termination: bind ASGI only to loopback/private socket, configure
    # uvicorn forwarded_allow_ips to the fixed trusted proxy IP (never '*'), and
    # make that proxy overwrite Host/X-Forwarded-Proto, stripping client values.
    # Only the resulting ASGI scope is authoritative at this layer.
    # The configured origin is the authority, not caller-selected proxy headers.
    actual = f"{request.url.scheme}://{request.url.netloc}"
    if actual != origin:
        raise CredentialBoundaryError("server_origin_mismatch", 403)
    incoming = request.headers.get("origin")
    # MV3 service-worker host permission needs no CORS. Disallow ordinary web
    # origins; extension requests may omit Origin or use their extension origin.
    if incoming and incoming != origin and extension_origin(incoming) is None:
        raise CredentialBoundaryError("request_origin_rejected", 403)
    return ingestion


def device(request: Request, ingestion) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise CredentialBoundaryError("invalid_device_token", 401)
    token = auth[7:]
    if len(token) > 256:
        raise CredentialBoundaryError("invalid_device_token", 401)
    return ingestion.devices.authenticate(token)


async def body(request: Request, model):
    data = bytearray()

    async def collect():
        async for chunk in request.stream():
            if len(data) + len(chunk) > LIMIT:
                raise CredentialBoundaryError("body_too_large", 413)
            data.extend(chunk)

    try:
        await asyncio.wait_for(collect(), 10)
    except asyncio.TimeoutError:
        raise CredentialBoundaryError("body_timeout", 408) from None
    try:
        return model.model_validate_json(data)
    except (ValidationError, ValueError, TypeError):
        raise CredentialBoundaryError("invalid_payload", 422) from None


class CredentialRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request: Request):
            try:
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                response.headers["Vary"] = "Origin"
                allowed = extension_origin(request.headers.get("origin", ""))
                # Scoped CORS: only the paired device's own extension origin is
                # echoed. No wildcard, no credentials, no other browser origin.
                if allowed:
                    response.headers["Access-Control-Allow-Origin"] = allowed
                return response
            except CredentialBoundaryError as exc:
                return JSONResponse(
                    {"code": exc.code},
                    status_code=exc.status_code,
                    headers={"Cache-Control": "no-store", "Vary": "Origin"},
                )
            except Exception as exc:
                from ...errors import MuseConsoleError

                if isinstance(exc, MuseConsoleError):
                    return JSONResponse({"code": "unauthorized"}, status_code=exc.status_code)
                return JSONResponse({"code": "credential_service_unavailable"}, status_code=503)

        return safe


router.route_class = CredentialRoute


@router.options("/credential-ingestions")
@router.options("/credential-ingestions/{path:path}")
async def preflight(request: Request, path: str = ""):
    """Answer the MV3 JSON preflight; unrelated origins get no CORS headers."""
    if extension_origin(request.headers.get("origin", "")) is None:
        raise CredentialBoundaryError("request_origin_rejected", 403)
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Methods": PREFLIGHT_METHODS,
            "Access-Control-Allow-Headers": PREFLIGHT_HEADERS,
            "Access-Control-Max-Age": PREFLIGHT_MAX_AGE,
            "Access-Control-Allow-Origin": request.headers["origin"],
        },
    )


@router.get("/credential-ingestions/server")
async def server(request: Request):
    return service(request).devices.server()


@router.post("/credential-ingestions/pair")
async def pair(request: Request):
    ingestion = service(request)
    payload = await body(request, PairInput)
    return ingestion.devices.pair(payload.challenge, payload.server_id)


@router.post("/credential-ingestions/rotate")
async def rotate(request: Request):
    ingestion = service(request)
    return ingestion.devices.rotate(device(request, ingestion))


@router.post("/credential-ingestions")
async def ingest(request: Request):
    ingestion = service(request)
    own = device(request, ingestion)
    payload = await body(request, CredentialEnvelope)
    if payload.device_id != own:
        raise CredentialBoundaryError("device_mismatch", 403)
    return await ingestion.ingest(payload)


@router.get("/credential-ingestions/{capture_id}")
async def receipt(request: Request, capture_id: str):
    ingestion = service(request)
    return ingestion.devices.receipt(device(request, ingestion), capture_id)


@router.post("/admin/credential-devices/pairings")
async def challenge(request: Request):
    ingestion = service(request)
    require_admin_key(request)
    payload = await body(request, ChallengeInput)
    if any(ord(c) < 32 or ord(c) == 127 for c in payload.label):
        raise CredentialBoundaryError("invalid_label")
    result = ingestion.devices.challenge(
        label=payload.label, new_account_quota=payload.new_account_limit
    )
    return {
        **result,
        **ingestion.devices.server(),
        "server_origin": configured_origin(ingestion.settings),
    }


@router.get("/admin/credential-devices")
async def devices(request: Request):
    ingestion = service(request)
    require_admin_key(request)
    return {"data": ingestion.devices.list()}


@router.delete("/admin/credential-devices/{device_id}")
async def revoke(request: Request, device_id: str):
    ingestion = service(request)
    require_admin_key(request)
    return ingestion.devices.revoke(device_id)


@router.post("/admin/credential-devices/{device_id}/confirm")
async def confirmation(request: Request, device_id: str):
    ingestion = service(request)
    require_admin_key(request)
    payload = await body(request, ConfirmationInput)
    return ingestion.confirm(device_id, payload.capture_id, payload.account_id)

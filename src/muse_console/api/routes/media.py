from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from ...errors import NotFound, Unauthorized
from ...services.container import Services
from ..deps import get_services, require_api_key

router = APIRouter(tags=["media"])


@router.get("/v1/media/{name}")
async def get_media(
    name: str, request: Request, svc: Services = Depends(get_services)
) -> FileResponse:
    """authorize the owning key or a valid expiring bearer link before serving a file."""
    path = svc.media.path_of(name)
    owner = svc.media.owner_of(name)
    if request.headers.get("authorization") or request.headers.get("x-api-key"):
        require_api_key(request)
        if request.state.key_id != "admin" and (not owner or owner != request.state.key_id):
            raise NotFound("media not found")
    else:
        try:
            expires = int(request.query_params.get("expires", "0"))
        except ValueError:
            expires = 0
        signature = request.query_params.get("signature", "")
        if not svc.media.valid_signature(name, expires, signature):
            raise Unauthorized("media requires its owning API key or an unexpired signed URL")
        if owner not in ("admin", "legacy"):
            key = svc.keys.get(owner or "")
            if key is None or key.revoked:
                raise Unauthorized("media link is no longer valid")
    return FileResponse(
        path,
        media_type=svc.media.mime_of(name),
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )

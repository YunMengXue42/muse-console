from __future__ import annotations

import base64
import time
from dataclasses import replace

from fastapi import APIRouter, Depends, Request

from ...core.models import resolve_model
from ...drivers.base import ImageRequest, MediaResult
from ...errors import FeatureNotImplemented
from ...services.container import Services
from ..deps import get_services, public_base, require_api_key
from ..schemas import ImageGenerationRequest

router = APIRouter(tags=["images"], dependencies=[Depends(require_api_key)])


def _image_item(r: MediaResult, fmt: str, svc: Services, base: str) -> dict:
    item: dict = {"revised_prompt": r.revised_prompt}
    if fmt == "b64_json":
        item["b64_json"] = base64.b64encode(r.data).decode()
    else:
        name = svc.media.save(r.data, r.mime, prefix="img")
        item["url"] = f"{base}/v1/media/{name}"
    return item


# Asking muse.ai for a "transparent background" makes it reply with text only, so
# ask for an easy-to-cut plain background and remove it locally instead.
_CUTOUT_HINT = "Isolated subject on a plain, uncluttered studio background."


@router.post("/v1/images/generations")
async def generate_images(body: ImageGenerationRequest, request: Request,
                          svc: Services = Depends(get_services)) -> dict:
    spec = resolve_model(body.model, "image")
    transparent = body.background == "transparent"
    prompt = f"{body.prompt.rstrip('. ')}. {_CUTOUT_HINT}" if transparent else body.prompt
    req = ImageRequest(prompt=prompt, model=spec.id, size=body.size, n=body.n,
                       timeout=svc.settings.image_timeout)
    results = await svc.gateway.generate_image(req)
    if transparent:
        # After the gateway call, so the account is not held during matting.
        results = [replace(r, data=await svc.matting.remove(r.data), mime="image/png")
                   for r in results]
    base = public_base(request)
    return {"created": int(time.time()),
            "data": [_image_item(r, body.response_format, svc, base) for r in results]}


@router.post("/v1/images/edits")
async def edit_images() -> None:
    # TODO(contributors): parse multipart (image[], prompt, size, response_format), load files
    # into ImageRequest.reference_images and reuse generate_images' response shaping.
    raise FeatureNotImplemented("/v1/images/edits is planned")

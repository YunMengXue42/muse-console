from __future__ import annotations

import base64
import mimetypes
import time
from dataclasses import replace

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ...core.media import load_image_ref
from ...core.models import resolve_model
from ...drivers.base import ImageRequest, InputImage, MediaResult
from ...errors import InvalidRequest
from ...services.container import Services
from ..deps import get_services, public_base, require_api_key
from ..schemas import ImageGenerationRequest

router = APIRouter(tags=["images"], dependencies=[Depends(require_api_key)])

# Reference images attached to one muse.ai message.
MAX_REFERENCE_IMAGES = 4

# Asking muse.ai for a "transparent background" makes it reply with text only, so
# ask for an easy-to-cut plain background and remove it locally instead.
_CUTOUT_HINT = "Isolated subject on a plain, uncluttered studio background."


def _image_item(r: MediaResult, fmt: str, svc: Services, base: str) -> dict:
    item: dict = {"revised_prompt": r.revised_prompt}
    if fmt == "b64_json":
        item["b64_json"] = base64.b64encode(r.data).decode()
    else:
        name = svc.media.save(r.data, r.mime, prefix="img")
        item["url"] = f"{base}/v1/media/{name}"
    return item


async def _generate(body: ImageGenerationRequest, refs: list[InputImage],
                    request: Request, svc: Services) -> dict:
    if len(refs) > MAX_REFERENCE_IMAGES:
        raise InvalidRequest(f"at most {MAX_REFERENCE_IMAGES} reference images are supported")
    spec = resolve_model(body.model, "image")
    transparent = body.background == "transparent"
    prompt = f"{body.prompt.rstrip('. ')}. {_CUTOUT_HINT}" if transparent else body.prompt
    req = ImageRequest(prompt=prompt, model=spec.id, size=body.size, n=body.n,
                       reference_images=refs, timeout=svc.settings.image_timeout)
    results = await svc.gateway.generate_image(req)
    if transparent:
        # After the gateway call, so the account is not held during matting.
        results = [replace(r, data=await svc.matting.remove(r.data), mime="image/png")
                   for r in results]
    base = public_base(request)
    return {"created": int(time.time()),
            "data": [_image_item(r, body.response_format, svc, base) for r in results]}


@router.post("/v1/images/generations")
async def generate_images(body: ImageGenerationRequest, request: Request,
                          svc: Services = Depends(get_services)) -> dict:
    refs = body.image if isinstance(body.image, list) else [body.image] if body.image else []
    loaded = [InputImage(*(await load_image_ref(ref))) for ref in refs]
    return await _generate(body, loaded, request, svc)


@router.post("/v1/images/edits")
async def edit_images(request: Request, svc: Services = Depends(get_services)) -> dict:
    """OpenAI-style multipart edit: ``image`` / ``image[]`` files plus a prompt.

    muse.ai has no inpainting, so the images act as references for a new image
    and ``mask`` is rejected rather than silently ignored.
    """
    form = await request.form()
    if form.get("mask") is not None:
        raise InvalidRequest("mask is not supported; muse.ai cannot inpaint a region")
    files = [f for key in ("image", "image[]") for f in form.getlist(key)
             if isinstance(f, UploadFile)]
    if not files:
        raise InvalidRequest("at least one image file is required (field 'image' or 'image[]')")
    fields = {k: v for k, v in form.items() if isinstance(v, str) and k not in ("image", "image[]")}
    try:
        body = ImageGenerationRequest(**fields)
    except ValidationError as exc:
        raise InvalidRequest(exc.errors()[0].get("msg", "invalid request")) from exc
    refs = []
    for f in files:
        mime = f.content_type or ""
        if not mime.startswith("image/"):
            mime = mimetypes.guess_type(f.filename or "")[0] or "image/png"
        refs.append(InputImage(data=await f.read(), mime=mime))
    return await _generate(body, refs, request, svc)

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
from ...errors import CapacityExceeded, InvalidRequest, NotFound
from ...services.container import Services
from ...services.tasks import Task
from ..deps import get_services, public_base, require_api_key
from ..schemas import ImageGenerationRequest

router = APIRouter(tags=["images"], dependencies=[Depends(require_api_key)])

# Reference images attached to one muse.ai message.
MAX_REFERENCE_IMAGES = 4

# Asking muse.ai for a "transparent background" makes it reply with text only, so
# ask for an easy-to-cut plain background and remove it locally instead.
_CUTOUT_HINT = "Isolated subject on a plain, uncluttered studio background."


def _image_item(r: MediaResult, fmt: str, svc: Services, base: str, owner: str) -> dict:
    if len(r.data) > svc.settings.media_max_bytes:
        raise CapacityExceeded("generated media exceeds the file size limit")
    item: dict = {"revised_prompt": r.revised_prompt}
    if fmt == "b64_json":
        item["b64_json"] = base64.b64encode(r.data).decode()
    else:
        name = svc.media.save(r.data, r.mime, prefix="img", owner_key_id=owner)
        item["url"] = svc.media.signed_url(name, base)
    return item


def _task_view(task: Task) -> dict:
    return {
        "id": task.id,
        "object": "image.task",
        "status": task.status.value,
        "progress": task.progress,
        "created_at": int(task.created_at),
        "model": task.request.get("model"),
        "result": task.result,
        "error": task.error,
    }


async def _generate(
    body: ImageGenerationRequest, refs: list[InputImage], request: Request, svc: Services
) -> dict:
    request.state.model = body.model  # logged even if it does not resolve
    request.state.model = body.model or resolve_model(body.model, "image").id
    if body.model:
        resolve_model(body.model, "image")  # reject before a task is queued
    if len(refs) > MAX_REFERENCE_IMAGES:
        raise InvalidRequest(f"at most {MAX_REFERENCE_IMAGES} reference images are supported")
    if body.async_:
        return _submit(body, refs, request, svc)
    return await _run(body, refs, public_base(request), svc, request.state.key_id)


def _submit(
    body: ImageGenerationRequest, refs: list[InputImage], request: Request, svc: Services
) -> dict:
    """Run the generation as a background task; results are stored as media URLs."""
    spec = resolve_model(body.model, "image")
    body = body.model_copy(update={"response_format": "url"})  # keep base64 out of tasks.json
    base = public_base(request)
    owner = request.state.key_id

    async def runner(progress) -> dict:
        return await _run(body, refs, base, svc, owner)

    meta = {
        "model": spec.id,
        "prompt": body.prompt,
        "size": body.size,
        "n": body.n,
        "references": len(refs),
    }
    task = svc.tasks.submit("image", meta, runner, owner_key_id=owner)
    request.state.task_id = task.id
    return _task_view(task)


async def _run(
    body: ImageGenerationRequest, refs: list[InputImage], base: str, svc: Services, owner: str
) -> dict:
    spec = resolve_model(body.model, "image")
    transparent = body.background == "transparent"
    prompt = f"{body.prompt.rstrip('. ')}. {_CUTOUT_HINT}" if transparent else body.prompt
    req = ImageRequest(
        prompt=prompt,
        model=spec.id,
        size=body.size,
        n=body.n,
        reference_images=refs,
        timeout=svc.settings.image_timeout,
    )
    results = await svc.gateway.generate_image(req)
    if transparent:
        # After the gateway call, so the account is not held during matting.
        results = [
            replace(r, data=await svc.matting.remove(r.data), mime="image/png") for r in results
        ]
    return {
        "created": int(time.time()),
        "data": [_image_item(r, body.response_format, svc, base, owner) for r in results],
    }


@router.post("/v1/images/generations")
async def generate_images(
    body: ImageGenerationRequest, request: Request, svc: Services = Depends(get_services)
) -> dict:
    refs = body.image if isinstance(body.image, list) else [body.image] if body.image else []
    if len(refs) > MAX_REFERENCE_IMAGES:
        raise InvalidRequest(f"at most {MAX_REFERENCE_IMAGES} reference images are supported")
    loaded = [
        InputImage(*(await load_image_ref(ref, max_bytes=svc.settings.input_image_max_bytes)))
        for ref in refs
    ]
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
    files = [
        f for key in ("image", "image[]") for f in form.getlist(key) if isinstance(f, UploadFile)
    ]
    if not files:
        raise InvalidRequest("at least one image file is required (field 'image' or 'image[]')")
    if len(files) > MAX_REFERENCE_IMAGES:
        raise InvalidRequest(f"at most {MAX_REFERENCE_IMAGES} reference images are supported")
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
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}:
            raise InvalidRequest("unsupported image MIME type")
        data = await f.read(svc.settings.input_image_max_bytes + 1)
        if not data or len(data) > svc.settings.input_image_max_bytes:
            raise InvalidRequest("input image is empty or exceeds the size limit")
        refs.append(InputImage(data=data, mime=mime))
    return await _generate(body, refs, request, svc)


@router.get("/v1/images/generations/{task_id}")
async def get_image_task(
    task_id: str, request: Request, svc: Services = Depends(get_services)
) -> dict:
    task = svc.tasks.get_owned(task_id, request.state.key_id)
    if task is None or task.kind != "image":
        raise NotFound(f"image task '{task_id}' not found")
    request.state.model = task.request.get("model")
    view = _task_view(task)
    view["result"] = svc.media.refresh_links(
        task.result, public_base(request), request.state.key_id
    )
    return view

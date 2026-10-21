from __future__ import annotations

import base64
import logging
import mimetypes
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ...core.media import load_image_ref
from ...core.models import resolve_model
from ...drivers.base import InputImage, VideoRequest
from ...errors import InvalidRequest, NotFound
from ...services.container import Services
from ...services.tasks import Task, TaskStatus
from ..deps import get_services, public_base, require_api_key
from ..schemas import VideoCreateRequest

log = logging.getLogger(__name__)

router = APIRouter(tags=["videos"], dependencies=[Depends(require_api_key)])

# Multipart clients (infinite-canvas and other OpenAI-compatible SDKs) post the
# same generation as a form instead of JSON: text controls plus optional frames.
_FORM_TEXT = ("prompt", "model", "size", "duration", "seconds")
_IGNORED_FORM_TEXT = ("mode", "resolution_name", "quality", "style", "background")
_UNSUPPORTED_FORM_PARTS = ("last_frame", "video[]", "videos", "audio[]", "audios")
_FRAME_PARTS = ("first_frame", "image[]", "image", "input_reference")


def _task_view(task: Task) -> dict:
    view = {
        "id": task.id,
        "object": "video",
        "status": task.status.value,
        "progress": task.progress,
        "created_at": int(task.created_at),
        "model": task.request.get("model"),
        "result": task.result,
        "error": task.error,
    }
    # Clients that follow the OpenAI video shape read the finished asset from the
    # top level, so mirror our stored URL there instead of nesting it only.
    if task.status is TaskStatus.SUCCEEDED and isinstance(task.result, dict):
        url = task.result.get("url")
        if isinstance(url, str) and url:
            view["video_url"] = url
            view["url"] = url
        if task.result.get("mime"):
            view["mime"] = task.result["mime"]
    return view


def _invalid(message: str, *loc: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", *loc), "msg": message, "input": None}]
    )


def _validate(payload: object) -> VideoCreateRequest:
    if not isinstance(payload, dict):
        raise _invalid("Input should be a valid dictionary or object")
    try:
        return VideoCreateRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(
            [dict(item, loc=("body", *item["loc"])) for item in exc.errors(include_url=False)]
        ) from None


async def _frame_from_upload(upload: UploadFile, max_bytes: int) -> str:
    data = await upload.read(max_bytes + 1)
    if not data:
        raise InvalidRequest("reference image is empty")
    if len(data) > max_bytes:
        raise InvalidRequest("reference image exceeds the size limit")
    mime = (upload.content_type or "").split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        mime = (mimetypes.guess_type(upload.filename or "")[0] or "").lower()
    if not mime.startswith("image/"):
        raise InvalidRequest("reference must be an image")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def _form_payload(request: Request, max_bytes: int) -> dict:
    form = await request.form()
    try:
        payload: dict = {}
        for name in _FORM_TEXT:
            value = form.get(name)
            if isinstance(value, str):
                text = value if name == "prompt" else value.strip()
                if text.strip() or name == "prompt":
                    payload[name] = text
        for name in _UNSUPPORTED_FORM_PARTS:
            if name in form:
                raise InvalidRequest(
                    "reference video, reference audio and last-frame input are not supported"
                )
        for name in _FRAME_PARTS:
            value = form.get(name)
            if value is None:
                continue
            payload.setdefault(
                "image",
                value if isinstance(value, str) else await _frame_from_upload(value, max_bytes),
            )
            break
        for name in _IGNORED_FORM_TEXT:
            if name in form:
                log.info("multipart video control %s ignored", name)
        return payload
    finally:
        await form.close()


@router.post("/v1/videos")
async def create_video(request: Request, svc: Services = Depends(get_services)) -> dict:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
        payload = await _form_payload(request, svc.settings.input_image_max_bytes)
    else:
        try:
            payload = await request.json()
        except ValueError:
            raise _invalid("Input should be a valid dictionary or object") from None
    body = _validate(payload)

    request.state.model = body.model  # logged even if it does not resolve
    spec = resolve_model(body.model, "video")
    request.state.model = body.model or spec.id
    frame_ref = body.image or body.input_reference
    first_frame = (
        InputImage(*(await load_image_ref(frame_ref, max_bytes=svc.settings.input_image_max_bytes)))
        if frame_ref
        else None
    )
    base = public_base(request)
    owner = request.state.key_id

    async def runner(progress) -> dict:
        req = VideoRequest(
            prompt=body.prompt,
            model=spec.id,
            size=body.size,
            duration=body.duration or body.seconds,
            first_frame=first_frame,
            timeout=svc.settings.video_timeout,
            on_progress=progress,
        )
        result = await svc.gateway.generate_video(req)
        name = svc.media.save(result.data, result.mime, prefix="vid", owner_key_id=owner)
        return {
            "url": svc.media.signed_url(name, base),
            "mime": result.mime,
            "width": result.width,
            "height": result.height,
        }

    request_meta = {
        "model": spec.id,
        "prompt": body.prompt,
        "size": body.size,
        "duration": body.duration or body.seconds,
        "has_first_frame": bool(first_frame),
    }
    task = svc.tasks.submit("video", request_meta, runner, owner_key_id=owner)
    request.state.task_id = task.id
    return _task_view(task)


async def _owned_video(task_id: str, request: Request, svc: Services) -> Task:
    task = svc.tasks.get_owned(task_id, request.state.key_id)
    if task is None or task.kind != "video":
        raise NotFound(f"video task '{task_id}' not found")
    return task


@router.get("/v1/videos/{task_id}")
async def get_video(task_id: str, request: Request, svc: Services = Depends(get_services)) -> dict:
    task = await _owned_video(task_id, request, svc)
    request.state.model = task.request.get("model")
    view = _task_view(task)
    view["result"] = svc.media.refresh_links(
        task.result, public_base(request), request.state.key_id
    )
    if "video_url" in view:
        view["video_url"] = view["result"]["url"]
        view["url"] = view["result"]["url"]
    return view


@router.get("/v1/videos/{task_id}/content")
async def get_video_content(
    task_id: str, request: Request, svc: Services = Depends(get_services)
) -> FileResponse:
    """Stream the finished asset for clients that download it instead of the URL."""
    task = await _owned_video(task_id, request, svc)
    result = task.result if isinstance(task.result, dict) else {}
    url = result.get("url")
    if task.status is not TaskStatus.SUCCEEDED or not isinstance(url, str) or not url:
        raise NotFound(f"video task '{task_id}' has no content yet")
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    path = svc.media.path_for(name, owner_key_id=request.state.key_id)
    if path is None:
        raise NotFound(f"video task '{task_id}' has no content yet")
    media_type = result.get("mime") or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=name)

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ...core.media import load_image_ref
from ...core.models import resolve_model
from ...core.prompt import flatten_messages, message_turns
from ...drivers.base import ChatRequest, InputImage
from ...errors import InvalidRequest, MuseConsoleError
from ...services.container import Services
from ..deps import get_services, require_api_key
from ..schemas import ChatCompletionRequest, validate_message

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"], dependencies=[Depends(require_api_key)])


def _approx_tokens(text: str) -> int:
    # Text-only heuristic, not upstream tokenizer usage; image cost is unknown.
    return (len(text) + 3) // 4


def _sse(payload: dict | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


@router.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest, request: Request, svc: Services = Depends(get_services)
):
    request.state.model = body.model  # logged even if it does not resolve
    spec = resolve_model(body.model, "chat")
    request.state.model = body.model or spec.id
    messages = [m.model_dump() for m in body.messages]
    for index, message in enumerate(messages):
        validate_message(message, f"messages[{index}]")
    flat = flatten_messages(messages)
    if not flat.text and not flat.images:
        raise InvalidRequest("messages contain no text or images")

    if len(flat.images) > 4:
        raise InvalidRequest("at most 4 input images are supported")
    if flat.images and not svc.driver.capabilities.chat_images:
        raise InvalidRequest(f"driver '{svc.driver.name}' does not support chat image input")
    images = [
        InputImage(*(await load_image_ref(ref, max_bytes=svc.settings.input_image_max_bytes)))
        for ref in flat.images
    ]
    cancel = asyncio.Event()
    owner = json.dumps([request.state.key_id, body.user], ensure_ascii=False, separators=(",", ":"))
    conversation_key = hashlib.sha256(owner.encode()).hexdigest()
    req = ChatRequest(
        prompt=flat.text,
        model=spec.id,
        images=images,
        timeout=svc.settings.chat_timeout,
        first_token_timeout=svc.settings.first_token_timeout,
        conversation_hint=conversation_key,
        turns=message_turns([m.model_dump() for m in body.messages]),
        cancel=cancel,
    )
    completion_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model_name = body.model or spec.id
    stream = svc.gateway.chat_stream(req)

    if not body.stream:
        try:
            text = "".join([d async for d in stream])
        finally:
            cancel.set()
            await stream.aclose()
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": _approx_tokens(flat.text),
                "completion_tokens": _approx_tokens(text),
                "total_tokens": _approx_tokens(flat.text) + _approx_tokens(text),
            },
        }

    # Pull the first delta before committing to a 200 so that account/upstream
    # failures surface as proper HTTP errors instead of a broken event stream.
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        first = ""
    except BaseException:
        cancel.set()
        await stream.aclose()
        raise

    def chunk(delta: dict, finish: str | None = None) -> dict:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    async def events() -> AsyncIterator[bytes]:
        produced = first
        try:
            yield _sse(chunk({"role": "assistant", "content": first}))
            async for delta in stream:
                produced += delta
                yield _sse(chunk({"content": delta}))
            yield _sse(chunk({}, "stop"))
            if (body.stream_options or {}).get("include_usage"):
                usage = {
                    "prompt_tokens": _approx_tokens(flat.text),
                    "completion_tokens": _approx_tokens(produced),
                }
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                yield _sse(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [],
                        "usage": usage,
                    }
                )
        except MuseConsoleError as exc:
            yield _sse(exc.to_body())
        except Exception:  # noqa: BLE001
            log.exception("stream crashed")
            yield _sse({"error": {"message": "internal error", "type": "server_error"}})
        finally:
            cancel.set()
            await stream.aclose()
        yield _sse("[DONE]")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

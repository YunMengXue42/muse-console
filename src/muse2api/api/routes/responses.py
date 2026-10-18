"""Text-only OpenAI Responses API adapter over the chat gateway."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ...core.media import load_image_ref
from ...core.models import resolve_model
from ...core.prompt import flatten_messages, message_turns
from ...drivers.base import ChatRequest, InputImage
from ...errors import InvalidRequest, Muse2APIError
from ...services.container import Services
from ..deps import get_services, require_api_key
from ..schemas import ResponsesRequest

log = logging.getLogger(__name__)
router = APIRouter(tags=["responses"], dependencies=[Depends(require_api_key)])

_ROLES = {"system", "developer", "user", "assistant"}
_TEXT_PARTS = {"input_text", "output_text", "text"}


def _messages(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if not isinstance(value, list) or not value:
        raise InvalidRequest("input must be a non-empty string or array of message items")

    messages: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or item.get("type", "message") != "message":
            raise InvalidRequest(
                f"input[{index}] must be a message item; tools and other items are unsupported"
            )
        role = item.get("role")
        if role not in _ROLES:
            raise InvalidRequest(
                f"input[{index}].role must be system, developer, user or assistant"
            )
        content = item.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise InvalidRequest(f"input[{index}].content must be a string or content-part array")

        parts: list[dict[str, Any]] = []
        for part_index, part in enumerate(content):
            location = f"input[{index}].content[{part_index}]"
            if not isinstance(part, dict):
                raise InvalidRequest(f"{location} must be an object")
            kind = part.get("type")
            if kind in _TEXT_PARTS:
                text = part.get("text")
                if not isinstance(text, str):
                    raise InvalidRequest(f"{location}.text must be a string")
                parts.append({"type": kind, "text": text})
            elif kind in ("input_image", "image_url"):
                ref = part.get("image_url")
                if isinstance(ref, dict):
                    ref = ref.get("url")
                if not isinstance(ref, str) or not ref:
                    raise InvalidRequest(f"{location}.image_url must be a URL or data URL")
                parts.append({"type": kind, "image_url": ref})
            else:
                raise InvalidRequest(f"{location}.type '{kind}' is unsupported")
        messages.append({"role": role, "content": parts})
    return messages


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _usage(prompt: str, output: str) -> dict[str, Any]:
    input_tokens = _approx_tokens(prompt)
    output_tokens = _approx_tokens(output)
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
    }


def _output_item(text: str, item_id: str, *, status: str = "completed") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "role": "assistant",
        "status": status,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _response(
    *,
    response_id: str,
    model: str,
    created_at: int,
    instructions: str | None,
    item: dict[str, Any] | None = None,
    status: str = "in_progress",
    usage: dict[str, Any] | None = None,
    completed_at: int | None = None,
    metadata: dict[str, Any] | None = None,
    user: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": instructions,
        "metadata": metadata,
        "model": model,
        "output": [item] if item else [],
        "parallel_tool_calls": False,
        "temperature": None,
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "usage": usage,
    }
    if completed_at is not None:
        result["completed_at"] = completed_at
    if user is not None:
        result["user"] = user
    return result


def _event(event_type: str, sequence: int, **values: Any) -> bytes:
    payload = {"type": event_type, "sequence_number": sequence, **values}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {data}\n\n".encode()


async def _chat_request(
    body: ResponsesRequest, request: Request, svc: Services
) -> tuple[ChatRequest, str, str, str, list[str]]:
    request.state.model = body.model
    spec = resolve_model(body.model, "chat")
    model_name = body.model or spec.id

    if body.previous_response_id or body.conversation:
        raise InvalidRequest("response state is not supported; send the full input on each request")
    if body.tools or body.tool_choice not in (None, "auto"):
        raise InvalidRequest("tools and non-default tool_choice are not supported")

    messages = _messages(body.input)
    if body.instructions:
        messages.insert(0, {"role": "system", "content": body.instructions})
    flat = flatten_messages(messages)
    if not flat.text and not flat.images:
        raise InvalidRequest("input contains no text or images")

    images = [InputImage(*(await load_image_ref(ref))) for ref in flat.images]
    owner = json.dumps([request.state.key_id, body.user], ensure_ascii=False, separators=(",", ":"))
    conversation_key = hashlib.sha256(owner.encode()).hexdigest()
    cancel = asyncio.Event()
    chat = ChatRequest(
        prompt=flat.text,
        model=spec.id,
        images=images,
        timeout=svc.settings.chat_timeout,
        first_token_timeout=svc.settings.first_token_timeout,
        conversation_hint=conversation_key,
        turns=message_turns(messages),
        cancel=cancel,
    )
    response_id = "resp_" + uuid.uuid4().hex[:24]
    item_id = "msg_" + uuid.uuid4().hex[:24]
    request.state.model = model_name
    return (
        chat,
        model_name,
        response_id,
        item_id,
        [flat.text, body.instructions or "", body.user or ""],
    )


@router.post("/v1/responses")
async def create_response(
    body: ResponsesRequest, request: Request, svc: Services = Depends(get_services)
):
    chat, model_name, response_id, item_id, usage_input = await _chat_request(body, request, svc)
    prompt = usage_input[0]
    created_at = int(time.time())
    stream = svc.gateway.chat_stream(chat)

    if not body.stream:
        chunks: list[str] = []
        try:
            async for delta in stream:
                chunks.append(delta)
        finally:
            chat.cancel.set()
            await stream.aclose()
        text = "".join(chunks)
        item = _output_item(text, item_id)
        return _response(
            response_id=response_id,
            model=model_name,
            created_at=created_at,
            instructions=body.instructions,
            item=item,
            status="completed",
            usage=_usage(prompt, text),
            completed_at=int(time.time()),
            metadata=body.metadata if isinstance(body.metadata, dict) else None,
            user=body.user,
        )

    try:
        first = await anext(stream)
    except StopAsyncIteration:
        first = ""
    except BaseException:
        chat.cancel.set()
        await stream.aclose()
        raise

    async def events() -> AsyncIterator[bytes]:
        sequence = 0

        def emit(event_type: str, **values: Any) -> bytes:
            nonlocal sequence
            event = _event(event_type, sequence, **values)
            sequence += 1
            return event

        partial_response = _response(
            response_id=response_id,
            model=model_name,
            created_at=created_at,
            instructions=body.instructions,
            metadata=body.metadata if isinstance(body.metadata, dict) else None,
            user=body.user,
        )
        yield emit("response.created", response=partial_response)
        yield emit("response.in_progress", response=partial_response)

        partial_item = _output_item("", item_id, status="in_progress")
        yield emit("response.output_item.added", output_index=0, item=partial_item)
        yield emit(
            "response.content_part.added",
            item_id=item_id,
            output_index=0,
            content_index=0,
            part={"type": "output_text", "text": "", "annotations": []},
        )
        produced: list[str] = []
        try:
            for delta in (first,):
                if delta:
                    produced.append(delta)
                    yield emit(
                        "response.output_text.delta",
                        item_id=item_id,
                        output_index=0,
                        content_index=0,
                        delta=delta,
                    )
            async for delta in stream:
                if delta:
                    produced.append(delta)
                    yield emit(
                        "response.output_text.delta",
                        item_id=item_id,
                        output_index=0,
                        content_index=0,
                        delta=delta,
                    )
            text = "".join(produced)
            final_item = _output_item(text, item_id)
            part = {"type": "output_text", "text": text, "annotations": []}
            yield emit(
                "response.output_text.done",
                item_id=item_id,
                output_index=0,
                content_index=0,
                text=text,
            )
            yield emit(
                "response.content_part.done",
                item_id=item_id,
                output_index=0,
                content_index=0,
                part=part,
            )
            yield emit("response.output_item.done", output_index=0, item=final_item)
            final_response = _response(
                response_id=response_id,
                model=model_name,
                created_at=created_at,
                instructions=body.instructions,
                item=final_item,
                status="completed",
                usage=_usage(prompt, text),
                completed_at=int(time.time()),
                metadata=body.metadata if isinstance(body.metadata, dict) else None,
                user=body.user,
            )
            yield emit("response.completed", response=final_response)
        except Muse2APIError as exc:
            yield emit("error", code=exc.code, message=exc.message, param=None)
        except Exception:  # noqa: BLE001
            log.exception("Responses API stream crashed")
            yield emit("error", code="internal_error", message="internal error", param=None)
        finally:
            chat.cancel.set()
            await stream.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

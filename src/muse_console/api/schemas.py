"""Request schemas and explicit boundaries for the chat-backed protocol subset."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..errors import InvalidRequest


def _neutral(value: Any, choices: tuple[Any, ...]) -> bool:
    # Do not confuse false/true with numeric controls 0/1.
    return any(type(value) is type(choice) and value == choice for choice in choices)


def validate_protocol(value: Any, *, responses: bool) -> Any:
    """Accept only inert compatibility controls, never promise driver enforcement."""
    if not isinstance(value, dict):
        return value
    # Null is a common SDK spelling of an omitted stream flag. Reject coercible
    # strings/numbers rather than accidentally enabling a different wire protocol.
    if "stream" in value:
        if not _neutral(value["stream"], (None, False, True)):
            raise InvalidRequest("stream must be a boolean or null")
        if value["stream"] is None:
            value = dict(value, stream=False)
    supported = {"model", "stream", "user"}
    supported |= {"input", "instructions", "metadata"} if responses else {"messages"}
    inert = {
        "temperature": (None, 1, 1.0),
        "top_p": (None, 1, 1.0),
        "frequency_penalty": (None, 0, 0.0),
        "presence_penalty": (None, 0, 0.0),
        "max_tokens": (None,),
        "max_completion_tokens": (None,),
        "max_output_tokens": (None,),
        "n": (None, 1),
        "stop": (None, [], ""),
        "tools": (None, []),
        "tool_choice": (None, "auto", "none"),
        "parallel_tool_calls": (None, False, True),
        "functions": (None, []),
        "function_call": (None, "auto", "none"),
        "logprobs": (None, False),
        "top_logprobs": (None, 0),
        "logit_bias": (None, {}),
        "seed": (None,),
        "store": (None, False),
        "response_format": (None, {"type": "text"}),
    }
    if responses:
        inert |= {
            "previous_response_id": (None,),
            "conversation": (None,),
            "background": (None, False),
            "truncation": (None, "disabled"),
            "include": (None, []),
            "text": (None, {}, {"format": {"type": "text"}}),
            "reasoning": (None, {}),
        }
    else:
        options = value.get("stream_options")
        if options is not None:
            if not isinstance(options, dict) or set(options) - {"include_usage"}:
                raise InvalidRequest("stream_options supports only include_usage")
            if not _neutral(options.get("include_usage"), (None, False, True)):
                raise InvalidRequest("stream_options.include_usage must be a boolean or null")
        supported.add("stream_options")
    for name, control in value.items():
        if name in supported:
            continue
        if name not in inert:
            raise InvalidRequest(f"{name} is unsupported by the chat-backed protocol")
        if not _neutral(control, inert[name]):
            raise InvalidRequest(
                f"{name} is unsupported except for null/inert defaults; "
                "the driver cannot enforce this control"
            )
    return value


def validate_message(message: dict[str, Any], location: str, *, responses: bool = False) -> None:
    """Validate before flattening, which must not silently discard protocol semantics."""
    allowed = {"role", "content"}
    inert = {
        "name": (None,),
        "tool_calls": (None, []),
        "tool_call_id": (None,),
        "function_call": (None,),
        "refusal": (None,),
        "audio": (None,),
    }
    if responses:
        inert |= {"type": ("message",), "status": (None, "completed"), "id": (None,)}
    for name, value in message.items():
        if name not in allowed and (name not in inert or not _neutral(value, inert[name])):
            raise InvalidRequest(f"{location}.{name} is unsupported")
    role = message.get("role")
    if role not in ("system", "developer", "user", "assistant"):
        raise InvalidRequest(f"{location}.role must be system, developer, user or assistant")
    content = message.get("content")
    if isinstance(content, str):
        return
    if not isinstance(content, list) or not content:
        raise InvalidRequest(f"{location}.content must be text or a non-empty content-part array")
    for index, part in enumerate(content):
        loc = f"{location}.content[{index}]"
        if not isinstance(part, dict):
            raise InvalidRequest(f"{loc} must be an object")
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            if set(part) - {"type", "text", "annotations"} or not isinstance(part.get("text"), str):
                raise InvalidRequest(f"{loc} supports only a string text field")
            if not _neutral(part.get("annotations"), (None, [])):
                raise InvalidRequest(f"{loc}.annotations are unsupported")
        elif kind in ("image_url", "input_image"):
            if role != "user":
                raise InvalidRequest(f"{loc}: images are supported only in user messages")
            if set(part) - {"type", "image_url", "detail"}:
                raise InvalidRequest(f"{loc} contains unsupported image fields")
            ref = part.get("image_url")
            if isinstance(ref, dict):
                if set(ref) - {"url", "detail"} or not _neutral(ref.get("detail"), (None, "auto")):
                    raise InvalidRequest(f"{loc}.image_url supports only url and auto detail")
                ref = ref.get("url")
            if not isinstance(ref, str) or not ref.strip():
                raise InvalidRequest(f"{loc}.image_url must be a non-empty image reference")
            if not _neutral(part.get("detail"), (None, "auto")):
                raise InvalidRequest(f"{loc}.detail supports only auto or null")
        else:
            raise InvalidRequest(f"{loc}.type '{kind}' is unsupported")


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="allow")


class ChatMessage(_Lenient):
    @model_validator(mode="before")
    @classmethod
    def check_message(cls, value: Any) -> Any:
        if isinstance(value, dict):
            validate_message(value, "messages[]")
        return value

    role: str
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None


class ChatCompletionRequest(_Lenient):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    user: str | None = None

    @model_validator(mode="before")
    @classmethod
    def protocol_boundary(cls, value: Any) -> Any:
        return validate_protocol(value, responses=False)

    # Only inert defaults are accepted; the driver has no sampling/tool controls.
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None


class ResponsesRequest(_Lenient):
    """Minimum Responses API request surface supported by the chat-backed gateway."""

    @model_validator(mode="before")
    @classmethod
    def protocol_boundary(cls, value: Any) -> Any:
        return validate_protocol(value, responses=True)

    model: str | None = None
    input: Any
    instructions: str | None = None
    stream: bool = False
    user: str | None = None
    metadata: dict[str, str] | None = None
    previous_response_id: str | None = None
    conversation: Any = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_references(self) -> ImageGenerationRequest:
        refs = (
            self.image
            if isinstance(self.image, list)
            else [self.image]
            if self.image is not None
            else []
        )
        if len(refs) > 4:
            raise ValueError("at most 4 reference images are supported")
        if any(not ref.strip() for ref in refs):
            raise ValueError(
                "image references must be non-empty strings; omit image or use null/[] for no references"
            )
        return self

    @field_validator("background")
    @classmethod
    def neutral_background(cls, value: str | None) -> str | None:
        # Clients that keep an unused picker send "" rather than omitting the field.
        if value in ("", "auto", "opaque", "transparent"):
            return value or None
        raise ValueError("background must be auto, opaque, transparent or empty")

    @field_validator("quality")
    @classmethod
    def neutral_quality(cls, value: str | None) -> str | None:
        # infinite-canvas sends quality=auto on every call; muse.ai has no such knob.
        if value in ("", "auto"):
            return None
        raise ValueError("quality is fixed by muse.ai; send auto or omit it")

    prompt: str = Field(min_length=1)
    model: str | None = None
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    response_format: Literal["url", "b64_json"] = "url"
    # Same values as OpenAI gpt-image-1. "transparent" cuts the subject out locally
    # and returns an RGBA PNG; "auto"/"opaque" leave the image as generated.
    background: str | None = None
    quality: str | None = None
    image: str | list[str] | None = Field(
        default=None, description="Reference image(s) as data URL, http(s) URL or base64 (max 4)"
    )
    # A sync call can outlive a proxy's timeout (Cloudflare cuts at ~100 s with 524), so
    # "async": true returns a task to poll at GET /v1/images/generations/{task_id}.
    async_: bool = Field(default=False, alias="async")
    user: str | None = None


class VideoCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def consistent_aliases(self) -> VideoCreateRequest:
        for name in ("image", "input_reference"):
            ref = getattr(self, name)
            if ref is not None and not ref.strip():
                raise ValueError(f"{name} must be a non-empty image reference or null")
        if self.duration is not None and self.seconds is not None and self.duration != self.seconds:
            raise ValueError("duration and seconds must agree when both are supplied")
        if (
            self.image is not None
            and self.input_reference is not None
            and self.image != self.input_reference
        ):
            raise ValueError("image and input_reference must agree when both are supplied")
        return self

    prompt: str = Field(min_length=1)
    model: str | None = None
    size: str | None = None
    duration: int | None = Field(default=None, ge=1, le=60)
    seconds: int | None = Field(default=None, ge=1, le=60)
    image: str | None = Field(default=None, description="First frame as data URL or http(s) URL")
    input_reference: str | None = None


class AccountCreate(BaseModel):
    label: str = ""
    cookies: dict[str, str]
    cookie_expires: dict[str, int] = Field(default_factory=dict)
    enabled: bool = True
    max_concurrency: int | None = Field(default=None, ge=1, le=16)


class AccountUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    cookies: dict[str, str] | None = None
    cookie_expires: dict[str, int] | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=16)


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


class KeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    revoked: bool | None = None

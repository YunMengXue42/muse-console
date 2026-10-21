from __future__ import annotations

import json

import pytest

from muse_console.errors import UpstreamError

ENDPOINTS = ["/v1/chat/completions", "/v1/responses"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_input_image_count_is_bounded_before_download(client, auth, monkeypatch, path):
    async def forbidden(*args, **kwargs):
        raise AssertionError("too many references must be rejected before any download")

    module = "responses" if path.endswith("responses") else "chat"
    monkeypatch.setattr(f"muse_console.api.routes.{module}.load_image_ref", forbidden)
    image = {"type": "image_url", "image_url": {"url": "https://example.invalid/pic.png"}}
    message = {"role": "user", "content": [image] * 5}
    body = {"input": [message]} if module == "responses" else {"messages": [message]}
    response = client.post(path, headers=auth, json=body)
    assert response.status_code == 400
    assert "at most 4" in response.json()["error"]["message"]


def payload(path):
    return (
        {"input": "hello"}
        if path.endswith("responses")
        else {"messages": [{"role": "user", "content": "hello"}]}
    )


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize(
    "control",
    [
        {"temperature": 0.5},
        {"top_p": 0.8},
        {"max_tokens": 20},
        {"max_completion_tokens": 20},
        {"max_output_tokens": 20},
        {"n": 2},
        {"stop": ["END"]},
        {"response_format": {"type": "json_object"}},
        {"tools": [{"type": "function"}]},
        {"tool_choice": "required"},
        {"frequency_penalty": 1},
        {"presence_penalty": -1},
        {"seed": 0},
        {"logprobs": True},
        {"top_logprobs": 1},
        {"logit_bias": {"1": 2}},
        {"functions": [{"name": "f"}]},
        {"function_call": {"name": "f"}},
        {"store": True},
        {"unknown_control": None},
        {"temperature": True},
        {"stream": "false"},
    ],
)
def test_effective_controls_rejected(client, auth, path, control):
    r = client.post(path, headers=auth, json=payload(path) | control)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert next(iter(control)) in r.json()["error"]["message"]


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("stream", [False, True, None])
def test_inert_defaults_and_basic_text(client, auth, path, stream):
    controls = dict(
        temperature=1,
        top_p=1.0,
        max_tokens=None,
        max_completion_tokens=None,
        max_output_tokens=None,
        n=1,
        stop=[],
        tools=[],
        tool_choice="none",
        parallel_tool_calls=True,
        functions=[],
        function_call="auto",
        frequency_penalty=0,
        presence_penalty=0.0,
        seed=None,
        logprobs=False,
        top_logprobs=0,
        logit_bias={},
        store=False,
        response_format={"type": "text"},
        stream=stream,
    )
    if path.endswith("responses"):
        controls |= dict(
            instructions="Be brief",
            metadata={"source": "test"},
            text={"format": {"type": "text"}},
            background=False,
            truncation="disabled",
            include=[],
            reasoning={},
        )
    else:
        controls["stream_options"] = {"include_usage": True}
    r = client.post(path, headers=auth, json=payload(path) | controls)
    assert r.status_code == 200, r.text
    if stream:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "response.completed" in r.text if path.endswith("responses") else "[DONE]" in r.text
    else:
        assert "hello" in r.text


@pytest.mark.parametrize(
    "control",
    [
        {"previous_response_id": "resp_old"},
        {"previous_response_id": ""},
        {"conversation": {}},
        {"conversation": "conv_old"},
        {"background": True},
        {"text": {"format": {"type": "json_schema", "schema": {}}}},
        {"text": {"verbosity": "low"}},
        {"reasoning": {"effort": "low"}},
        {"include": ["reasoning.encrypted_content"]},
        {"truncation": "auto"},
    ],
)
def test_responses_state_and_structured_controls(client, auth, control):
    r = client.post("/v1/responses", headers=auth, json={"input": "hello"} | control)
    assert r.status_code == 400


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize(
    "message",
    [
        {"role": "tool", "content": "x"},
        {"role": "function", "content": "x"},
        {"role": "unknown", "content": "x"},
        {"role": "assistant", "content": None},
        {"role": "user", "content": "x", "name": "named"},
        {"role": "assistant", "content": "x", "tool_calls": [{"id": "call_1"}]},
        {"role": "user", "content": "x", "tool_call_id": "call_1"},
        {"role": "assistant", "content": "x", "function_call": {"name": "f"}},
        {"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]},
        {"role": "user", "content": [{"type": "input_file", "file_id": "file_x"}]},
        {"role": "user", "content": [{"type": "text", "text": "x", "unknown": 1}]},
        {"role": "user", "content": [{"type": "text", "text": 123}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,eA==", "detail": "high"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "image_url", "image_url": "data:image/png;base64,eA=="}],
        },
        {"role": "user", "content": "x", "extra": None},
    ],
)
def test_message_boundaries(client, auth, path, message):
    key = "input" if path.endswith("responses") else "messages"
    r = client.post(path, headers=auth, json={key: [message]})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("stream", [False, True])
def test_images_roles_and_instructions_preserved(client, auth, monkeypatch, path, stream):
    seen = []

    async def fake(req):
        seen.append(req)
        yield "answer"

    monkeypatch.setattr(client.app.state.services.gateway, "chat_stream", fake)
    messages = [
        {"role": "system", "content": "system instruction"},
        {"role": "developer", "content": "developer instruction"},
        {"role": "assistant", "content": "previous", "tool_calls": []},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,eA==",
                    "detail": "auto",
                },
            ],
        },
    ]
    body = {
        "input" if path.endswith("responses") else "messages": messages,
        "stream": stream,
        "model": "gpt-4o",
    }
    if path.endswith("responses"):
        body["instructions"] = "extra instruction"
    r = client.post(path, headers=auth, json=body)
    assert r.status_code == 200, r.text
    req = seen[0]
    assert req.images[0].data == b"x"
    assert "system instruction" in req.prompt and "developer instruction" in req.prompt
    assert "previous" in req.prompt and "look" in req.prompt
    assert req.cancel.is_set()


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("early", [False, True])
def test_stream_error_is_not_success(client, auth, monkeypatch, path, early):
    seen = []

    async def broken(req):
        seen.append(req)
        if not early:
            yield "partial"
        raise UpstreamError("fake upstream failure")

    monkeypatch.setattr(client.app.state.services.gateway, "chat_stream", broken)
    r = client.post(path, headers=auth, json=payload(path) | {"stream": True})
    assert seen[0].cancel.is_set()
    if early:
        assert r.status_code == 502
        return
    assert r.status_code == 200
    assert "fake upstream failure" in r.text
    assert "response.completed" not in r.text
    if path.endswith("responses"):
        assert "response.failed" in r.text
    else:
        assert '"finish_reason": "stop"' not in r.text
        assert r.text.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("path", ENDPOINTS)
def test_empty_output_has_zero_estimated_tokens(client, auth, monkeypatch, path):
    async def empty(req):
        if False:
            yield ""

    monkeypatch.setattr(client.app.state.services.gateway, "chat_stream", empty)
    plain = client.post(path, headers=auth, json=payload(path)).json()
    usage = plain["usage"]
    output_key = "output_tokens" if path.endswith("responses") else "completion_tokens"
    assert usage[output_key] == 0
    r = client.post(path, headers=auth, json=payload(path) | {"stream": True})
    if path.endswith("responses"):
        events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
        assert events[-1]["type"] == "response.completed"
        assert events[-1]["response"]["usage"][output_key] == 0
    else:
        assert '"finish_reason": "stop"' in r.text and "[DONE]" in r.text


@pytest.mark.parametrize("path", ["/v1/images/generations", "/v1/videos"])
@pytest.mark.parametrize("control", [{"unknown": None}, {"quality": "hd"}, {"style": "vivid"}])
def test_media_unknown_controls_rejected(client, auth, path, control):
    r = client.post(path, headers=auth, json={"prompt": "x"} | control)
    assert r.status_code == 400


@pytest.mark.parametrize(
    "control",
    [
        {"duration": 5, "seconds": 6},
        {"image": "data:image/png;base64,eA==", "input_reference": "different"},
    ],
)
def test_video_conflicting_aliases_rejected(client, auth, control):
    assert (
        client.post("/v1/videos", headers=auth, json={"prompt": "x"} | control).status_code == 400
    )


@pytest.mark.parametrize("path", ENDPOINTS)
def test_driver_image_capability_rejected_before_loading(client, auth, monkeypatch, path):
    from muse_console.drivers.base import DriverCapabilities

    monkeypatch.setattr(
        client.app.state.services.driver, "capabilities", DriverCapabilities(chat=True)
    )
    key = "input" if path.endswith("responses") else "messages"
    r = client.post(
        path,
        headers=auth,
        json={
            key: [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": "data:image/png;base64,eA=="}],
                }
            ]
        },
    )
    assert r.status_code == 400
    assert "does not support chat image" in r.json()["error"]["message"]


def test_responses_non_message_item_rejected(client, auth):
    r = client.post(
        "/v1/responses",
        headers=auth,
        json={"input": [{"type": "function_call_output", "call_id": "call_x", "output": "x"}]},
    )
    assert r.status_code == 400


@pytest.mark.parametrize("image", ["", "   ", [""], ["data:image/png;base64,eA==", " "], ["x"] * 5])
def test_image_invalid_references_rejected_before_loading(client, auth, monkeypatch, image):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid references must not be loaded")

    monkeypatch.setattr("muse_console.api.routes.images.load_image_ref", forbidden)
    r = client.post("/v1/images/generations", headers=auth, json={"prompt": "x", "image": image})
    assert r.status_code == 400


@pytest.mark.parametrize("field", ["image", "input_reference"])
@pytest.mark.parametrize("ref", ["", "   "])
def test_video_empty_reference_rejected(client, auth, field, ref):
    r = client.post("/v1/videos", headers=auth, json={"prompt": "x", field: ref})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "control",
    [
        {"duration": 5, "seconds": 5},
        {"duration": None, "seconds": 5},
        {"duration": 5, "seconds": None},
        {"image": "data:image/png;base64,eA==", "input_reference": "data:image/png;base64,eA=="},
        {"image": None, "input_reference": "data:image/png;base64,eA=="},
    ],
)
def test_video_consistent_aliases_accepted(client, auth, control):
    r = client.post("/v1/videos", headers=auth, json={"prompt": "x"} | control)
    assert r.status_code == 200, r.text
    assert r.json()["object"] == "video"


@pytest.mark.parametrize("image", [None, []])
def test_image_no_reference_compatible(client, auth, image):
    r = client.post("/v1/images/generations", headers=auth, json={"prompt": "x", "image": image})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "control",
    [{"quality": "auto"}, {"quality": ""}, {"background": ""}, {"background": "auto"}],
)
def test_image_neutral_controls_compatible(client, auth, control):
    r = client.post("/v1/images/generations", headers=auth, json={"prompt": "x"} | control)
    assert r.status_code == 200, r.text


def test_image_infinite_canvas_payload_compatible(client, auth):
    body = {
        "model": "muse-image",
        "prompt": "x",
        "n": "1",
        "size": "1:1",
        "quality": "auto",
        "background": "",
        "response_format": "b64_json",
    }
    r = client.post("/v1/images/generations", headers=auth, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["data"][0].get("b64_json")


@pytest.mark.parametrize("tools", [None, []])
def test_responses_tool_choice_none_consistent(client, auth, tools):
    r = client.post(
        "/v1/responses",
        headers=auth,
        json={"input": "hello", "tools": tools, "tool_choice": "none"},
    )
    assert r.status_code == 200, r.text


def test_multipart_unknown_text_control_rejected(client, auth):
    r = client.post(
        "/v1/images/edits",
        headers=auth,
        data={"prompt": "x", "quality": "hd"},
        files={"image": ("a.png", b"x", "image/png")},
    )
    assert r.status_code == 400

import os

import pytest

from muse_console.core.media import MediaStore
from muse_console.core.models import resolve_model
from muse_console.core.prompt import flatten_messages, followup_text, message_turns
from muse_console.errors import InvalidRequest


def test_single_user_message_verbatim():
    assert flatten_messages([{"role": "user", "content": "hi"}]).text == "hi"


def test_multi_turn_has_roles_and_images():
    flat = flatten_messages(
        [
            {"role": "system", "content": "be brief"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
        ]
    )
    assert "[System]\nbe brief" in flat.text
    assert "[User]\nwhat is this" in flat.text
    assert flat.images == ["data:image/png;base64,AAAA"]


def test_followup_sends_only_new_user_text():
    first = message_turns([{"role": "user", "content": "hi"}])
    second = message_turns(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"},
        ]
    )
    assert followup_text(first, second) == "again"


def test_followup_rejects_a_different_history():
    first = message_turns([{"role": "user", "content": "hi"}])
    other = message_turns([{"role": "user", "content": "a different chat"}])
    edited = message_turns(
        [
            {"role": "user", "content": "hi, edited"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"},
        ]
    )
    assert followup_text(first, other) is None
    assert followup_text(first, edited) is None
    assert followup_text(first, first) is None


def test_thread_url_ignores_the_new_thread_page():
    from muse_console.drivers.browser.driver import is_thread_url

    assert is_thread_url("https://muse.ai/thread/abc")
    assert not is_thread_url("https://muse.ai/thread/new")
    assert not is_thread_url("https://muse.ai/thread/new/")
    assert not is_thread_url("https://muse.ai/")


def test_stream_delta_never_resends_the_reply():
    from muse_console.drivers.browser.driver import stream_delta

    emitted = ""
    sent = []
    # The page re-renders "-" into a bullet and inserts a zero-width space.
    for text in ["Name: Xi\n", "Name: Xi\n-", "Name: Xi\n-\u200bEmail", "Name: Xi\n• Email: a"]:
        delta, emitted = stream_delta(emitted, text)
        sent.append(delta)
    delta, emitted = stream_delta(emitted, "Name: Xi\n• Email: a@b", final=True)
    sent.append(delta)
    joined = "".join(sent)
    assert joined.count("Name: Xi") == 1
    assert "\u200b" not in joined
    assert joined.startswith("Name: Xi\n")


def test_stream_delta_holds_short_line_until_final():
    from muse_console.drivers.browser.driver import stream_delta

    delta, emitted = stream_delta("", "Hello\n-")
    assert delta == "Hello\n"
    delta, emitted = stream_delta(emitted, "Hello\n-", final=True)
    assert delta == "-" and emitted == "Hello\n-"


def test_resolve_model():
    from muse_console.errors import ModelNotFound

    assert resolve_model("gpt-4o", "chat").id == "muse-chat"
    assert resolve_model(None, "video").id == "muse-video"
    assert resolve_model("", "image").id == "muse-image"
    with pytest.raises(ModelNotFound) as exc:
        resolve_model("unknown", "image")
    assert exc.value.status_code == 404 and exc.value.code == "model_not_found"
    with pytest.raises(InvalidRequest):
        resolve_model("muse-image", "chat")


def test_media_prune_only_removes_expired_generated_files(tmp_path):
    store = MediaStore(tmp_path)
    old = tmp_path / "img_0123456789abcdef.png"
    fresh = tmp_path / "vid_fedcba9876543210.mp4"
    unrelated = tmp_path / "notes.txt"
    malformed = tmp_path / "img_not-a-generated-file.png"
    for path in (old, fresh, unrelated, malformed):
        path.write_bytes(b"data")

    now = 100_000.0
    os.utime(old, (now - 2 * 86400, now - 2 * 86400))
    os.utime(fresh, (now - 0.5 * 86400, now - 0.5 * 86400))
    os.utime(unrelated, (now - 10 * 86400, now - 10 * 86400))
    os.utime(malformed, (now - 10 * 86400, now - 10 * 86400))

    assert store.prune(1, now=now) == 1
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()
    assert malformed.exists()
    assert store.prune(0, now=now) == 0

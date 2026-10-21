import pytest

from muse_console.core.media import _EXT_BY_MIME, MediaStore


@pytest.mark.parametrize(("mime", "extension"), _EXT_BY_MIME.items())
def test_generated_media_types_do_not_depend_on_host_registry(monkeypatch, mime, extension):
    monkeypatch.setattr("muse_console.core.media.mimetypes.guess_type", lambda name: (None, None))
    assert MediaStore.mime_of("img_0123456789abcdef" + extension) == mime
    assert MediaStore.mime_of("img_0123456789abcdef" + extension.upper()) == mime


def test_unknown_media_type_is_not_guessed_from_content(monkeypatch):
    monkeypatch.setattr("muse_console.core.media.mimetypes.guess_type", lambda name: (None, None))
    assert MediaStore.mime_of("unknown.bin") == "application/octet-stream"

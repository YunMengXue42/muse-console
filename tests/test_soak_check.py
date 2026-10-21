"""Short loopback mock exercise, never a 72-hour acceptance claim."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from tools.soak_check import base_url, run


@pytest.fixture
def mock_server():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "ok": True,
                        "ready": True,
                        "driver": {
                            "ok": True,
                            "tabs": 3,
                            "busy_tabs": 1,
                            "identity": "PRIVATE-DRIVER",
                        },
                        "accounts": {"available": 2, "invalid": 1, "identity": "PRIVATE-ACCOUNT"},
                        "tasks": {"pending": 4, "running": 2, "reply": "PRIVATE-TASK"},
                        "identity": "PRIVATE-IDENTITY",
                        "reply": "PRIVATE-REPLY",
                    }
                ).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_short_readonly_mock(mock_server, tmp_path, monkeypatch):
    url, calls = mock_server
    monkeypatch.setenv("SOAK_TEST_KEY", "PRIVATE-KEY")
    report = tmp_path / "report.jsonl"
    start = time.monotonic()
    assert run(url, 0.3, 0.05, report, admin_key_env="SOAK_TEST_KEY")
    assert time.monotonic() - start < 3
    assert {path for path, _ in calls} == {"/healthz", "/readyz", "/admin/status"}
    assert all(
        auth == ("Bearer PRIVATE-KEY" if path == "/admin/status" else None) for path, auth in calls
    )
    text = report.read_text()
    assert not any(value in text for value in ("PRIVATE", "SOAK_TEST_KEY", url))
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[-1]["passed"] is True
    assert all(count > 0 for count in rows[-1]["samples"].values())
    admin = [row for row in rows if row.get("endpoint") == "/admin/status"]
    assert admin and all(
        row["counts"]
        == {
            "resources": {"tabs": 3, "busy_tabs": 1},
            "accounts": {"available": 2, "invalid": 1},
            "tasks": {"pending": 4, "running": 2},
        }
        for row in admin
    )


def test_default_no_admin(mock_server, tmp_path):
    url, calls = mock_server
    assert run(url, 0.2, 0.05, tmp_path / "report")
    assert calls
    assert all(path in {"/healthz", "/readyz"} and auth is None for path, auth in calls)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.org",
        "http://example.org",
        "https://example.org/path",
        "https://example.org?key=x",
        "https://example.org#fragment",
        "file:///tmp/a",
        "",
    ],
)
def test_invalid_origin(url):
    with pytest.raises(ValueError):
        base_url(url)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_timing(tmp_path, value):
    with pytest.raises(ValueError):
        run("http://127.0.0.1", value, 1, tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_existing_report_and_missing_key(mock_server, tmp_path, monkeypatch):
    url, calls = mock_server
    report = tmp_path / "report"
    report.write_text("keep")
    with pytest.raises(FileExistsError):
        run(url, 1, 1, report)
    monkeypatch.delenv("SOAK_TEST_MISSING", raising=False)
    with pytest.raises(ValueError):
        run(url, 1, 1, tmp_path / "new", admin_key_env="SOAK_TEST_MISSING")
    assert not calls
    assert report.read_text() == "keep"


def test_failure_and_redaction(tmp_path, monkeypatch):
    import httpx

    def respond(request):
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"ready": False, "secret": "DO-NOT-LOG"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    report = tmp_path / "report"
    assert not run("http://127.0.0.1", 0.03, 0.01, report)
    assert "DO-NOT-LOG" not in report.read_text()


@pytest.mark.parametrize("case", ["unhealthy", "oversized", "malformed", "bad-count", "missing"])
def test_admin_invalid_or_unhealthy_not_passed(tmp_path, monkeypatch, case):
    import httpx

    monkeypatch.setenv("SOAK_TEST_KEY", "PRIVATE-KEY")

    def respond(request):
        if request.url.path != "/admin/status":
            return httpx.Response(200, json={"ok": True, "ready": True})
        data = {
            "driver": {"ok": True, "tabs": 0, "busy_tabs": 0},
            "accounts": {"available": 0, "invalid": 0},
            "tasks": {"pending": 0, "running": 0},
            "identity": "PRIVATE-IDENTITY",
        }
        if case == "oversized":
            return httpx.Response(200, content=b"PRIVATE" * 10000)
        if case == "malformed":
            return httpx.Response(200, content=b"PRIVATE not JSON")
        if case == "unhealthy":
            data["driver"]["ok"] = False
        if case == "bad-count":
            data["accounts"]["available"] = "PRIVATE-ACCOUNT"
        if case == "missing":
            data.pop("driver")
        return httpx.Response(200, json=data)

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    report = tmp_path / "report"
    assert not run("http://127.0.0.1", 0.04, 0.01, report, admin_key_env="SOAK_TEST_KEY")
    assert "PRIVATE" not in report.read_text()
    assert json.loads(report.read_text().splitlines()[-1])["passed"] is False


def test_zero_samples_not_passed(tmp_path, monkeypatch):
    from tools import soak_check

    # Advance past the monitoring deadline before the first cycle, deterministically.
    clock = iter([0, 1, 2, 3])
    monkeypatch.setattr(soak_check.time, "monotonic", lambda: next(clock))
    report = tmp_path / "report"
    assert not run("http://127.0.0.1", 0.1, 0.01, report)
    end = json.loads(report.read_text().splitlines()[-1])
    assert end["passed"] is False
    assert end["samples"] == {"/healthz": 0, "/readyz": 0}


def test_redirect_not_followed(tmp_path, monkeypatch):
    import httpx

    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://not-authorized.invalid/"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond))
    )
    assert not run("http://127.0.0.1", 0.02, 0.01, tmp_path / "report")
    assert calls and all("not-authorized" not in call for call in calls)

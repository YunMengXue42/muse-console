"""Real loopback release plumbing, not live upstream or long-running acceptance."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
import uvicorn
from tools import backup_restore, soak_check

from muse_console import __version__
from muse_console.app import create_app
from muse_console.config import Settings
from muse_console.drivers.mock import MockDriver

_API_KEY = "release-smoke-synthetic-legacy"
_ADMIN_KEY = "release-smoke-synthetic-admin"
_PROMPT = "release-smoke-private-prompt"
_ADMIN_ENV = "RELEASE_SMOKE_ADMIN_KEY"


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


@contextmanager
def _serve(data_dir, *, previous_port=None):
    """Own the bound socket, event loop and thread, including startup failure cleanup."""
    settings = Settings(
        _env_file=None,
        driver="mock",
        data_dir=data_dir,
        api_key=_API_KEY,
        admin_key=_ADMIN_KEY,
        public_base="",
        keepalive_enabled=False,
        credential_ingestion_enabled=False,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, MockDriver(delay=0.01)),
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="on",
            log_config=None,
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=3,
        )
    )
    errors = []
    state = {}

    async def run(sock):
        state["loop"] = asyncio.get_running_loop()
        state["task"] = asyncio.current_task()
        await server.serve(sockets=[sock])

    def target(sock):
        try:
            asyncio.run(run(sock))
        except BaseException as exc:
            errors.append(exc)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Keep the actual selected socket; never probe a free port and reopen it.
        sock.bind(("127.0.0.1", 0))
        if sock.getsockname()[1] == previous_port:
            # Hold the first reservation while selecting a genuinely different origin.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as replacement:
                replacement.bind(("127.0.0.1", 0))
                sock.close()
                sock = replacement.dup()
        try:
            sock.listen(128)
            port = sock.getsockname()[1]
            thread = threading.Thread(target=target, args=(sock,), name="release-smoke-uvicorn")
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started:
                    assert thread.is_alive(), "uvicorn exited during startup"
                    assert time.monotonic() < deadline, "uvicorn startup timed out"
                    time.sleep(0.01)
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}",
                    timeout=2,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    yield client, port
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                if thread.is_alive():
                    # Bound the failure path too; asyncio.run cleans up tasks/executors.
                    loop, task = state.get("loop"), state.get("task")
                    if loop is not None and not loop.is_closed() and task is not None:
                        loop.call_soon_threadsafe(task.cancel)
                    thread.join(timeout=5)
                assert not thread.is_alive(), "owned server thread failed to stop"
                assert not errors, "uvicorn thread failed"
                assert not server.started or server.should_exit
        finally:
            sock.close()


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if tag in {"script", "link"} and name in {"src", "href"} and value:
                self.paths.add(value)


def _health_and_dashboard(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["ready"] is True
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assets = _Assets()
    assets.feed(response.text)
    assert any(path.endswith(".js") for path in assets.paths)
    assert any(path.endswith(".css") for path in assets.paths)
    for path in assets.paths:
        assert path.startswith("/assets/") and not path.startswith("//")
        response = client.get(path)
        assert response.status_code == 200
        assert response.content
        assert "text/html" not in response.headers["content-type"]


def _owned_task(client, path, owner, foreign):
    assert client.get(path, headers=foreign).status_code == 404
    response = client.get(path, headers=owner)
    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "succeeded"
    assert task["error"] is None
    return task["result"]["data"][0]["url"]


def _media(client, signed, owner, foreign):
    parts = urlsplit(signed)
    # Never follow a returned URL to another server, even on assertion failure.
    assert f"{parts.scheme}://{parts.netloc}" == str(client.base_url).rstrip("/")
    assert parts.query and parts.path.startswith("/v1/media/")
    path = urlunsplit(("", "", parts.path, parts.query, ""))
    public = client.get(path)
    assert public.status_code == 200
    assert public.headers["cache-control"] == "private, no-store"
    assert public.headers["content-type"].startswith("image/png")
    assert public.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(parts.path).status_code == 401
    assert client.get(parts.path, headers=foreign).status_code == 404
    owned = client.get(parts.path, headers=owner)
    assert owned.status_code == 200
    assert owned.content == public.content
    return public.content


def _soak_failure_summary(report):
    """Fixed categories/counts only: never include report lines, URLs or keys."""
    try:
        rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
        checks = [row for row in rows if row.get("type") == "check"]
        summary = {}
        for label, endpoint in (
            ("health", "/healthz"),
            ("ready", "/readyz"),
            ("admin", "/admin/status"),
        ):
            selected = [row for row in checks if row.get("endpoint") == endpoint]
            summary[label] = {
                "samples": len(selected),
                "failed": sum(row.get("ok") is not True for row in selected),
                "request_or_response_failed": sum(
                    row.get("error") == "request_or_response_failed" for row in selected
                ),
                "status_200_failed": sum(
                    row.get("status") == 200 and row.get("ok") is not True for row in selected
                ),
                "status_other": sum(row.get("status") != 200 for row in selected),
            }
        summary["end_present"] = bool(rows and rows[-1].get("type") == "end")
        summary["interrupted"] = bool(rows and rows[-1].get("interrupted") is True)
        return "SOAK_FAILED " + json.dumps(summary, sort_keys=True)
    except (OSError, ValueError, TypeError, AttributeError):
        return "SOAK_FAILED REPORT_UNAVAILABLE_OR_INVALID"


def test_release_smoke_loopback_backup_restore(tmp_path, monkeypatch):
    # Do not inherit a developer's data paths, browser flags, credentials or .env.
    for name in tuple(os.environ):
        if name.upper().startswith(("MUSE_CONSOLE_", "MUSE2API_")):
            monkeypatch.delenv(name)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def loopback_only(original):
        def connect(sock, address):
            if sock.family in {socket.AF_INET, socket.AF_INET6}:
                assert ipaddress.ip_address(address[0]).is_loopback, "non-loopback egress"
            return original(sock, address)

        return connect

    monkeypatch.setattr(socket.socket, "connect", loopback_only(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", loopback_only(original_connect_ex))
    monkeypatch.setenv(_ADMIN_ENV, _ADMIN_KEY)
    source = tmp_path / "fake-data"
    backup = tmp_path / "backup"
    restored = tmp_path / "restored-data"
    report = tmp_path / "soak.jsonl"

    with _serve(source) as (client, original_port):
        _health_and_dashboard(client)
        keys = []
        for name in ("smoke-owner", "smoke-foreign"):
            response = client.post("/admin/keys", headers=_bearer(_ADMIN_KEY), json={"name": name})
            assert response.status_code == 200
            keys.append(response.json()["api_key"])
        assert keys[0] != keys[1]
        owner, foreign = map(_bearer, keys)
        response = client.post(
            "/v1/images/generations",
            headers=owner,
            json={"prompt": _PROMPT, "async": True},
        )
        assert response.status_code == 200
        task_path = f"/v1/images/generations/{response.json()['id']}"
        deadline = time.monotonic() + 10
        while True:
            response = client.get(task_path, headers=owner)
            assert response.status_code == 200
            status = response.json()["status"]
            if status == "succeeded":
                break
            assert status in {"queued", "running"}
            assert time.monotonic() < deadline, "mock task timed out"
            time.sleep(0.02)
        signed = _owned_task(client, task_path, owner, foreign)
        media = _media(client, signed, owner, foreign)
        response = client.post(
            "/v1/images/generations",
            headers=owner,
            json={"prompt": "invalid", "unknown_release_control": True},
        )
        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert soak_check.run(
            str(client.base_url).rstrip("/"),
            0.3,
            0.05,
            report,
            admin_key_env=_ADMIN_ENV,
            timeout=1,
        ), _soak_failure_summary(report)
        text = report.read_text(encoding="utf-8")
        for secret in (_API_KEY, _ADMIN_KEY, *keys, _PROMPT, _ADMIN_ENV, signed, task_path):
            assert secret not in text
        assert str(client.base_url).rstrip("/") not in text
        rows = [json.loads(line) for line in text.splitlines()]
        assert rows[-1]["passed"] is True
        assert rows[-1]["interrupted"] is False
        assert set(rows[-1]["samples"]) == {"/healthz", "/readyz", "/admin/status"}
        assert all(count > 0 for count in rows[-1]["samples"].values())
        admin_rows = [row for row in rows if row.get("endpoint") == "/admin/status"]
        assert admin_rows
        for row in admin_rows:
            assert row["ok"] is True
            assert row["counts"] == {
                "resources": {},
                "accounts": {"available": 0, "invalid": 0},
                "tasks": {"pending": 0, "running": 0},
            }

    # _serve has stopped and joined ALL its writers before offline inventory/copy.
    inventory = backup_restore.inventory(source)
    assert {
        "accounts.db",
        "credentials.key",
        "credential-devices.db",
        "keys.json",
        "requests.db",
        "tasks.json",
        "media-owners.db",
        "media-signing.key",
    } <= inventory.keys()
    assert not backup.exists() and not restored.exists()
    backup_restore.backup(source, backup, stopped=True)
    backup_restore.restore(backup, restored, stopped=True)
    assert backup_restore.inventory(restored) == inventory
    assert backup_restore.inventory(source) == inventory
    assert backup_restore.inventory(backup / "payload") == inventory
    with pytest.raises(ValueError, match="already exists"):
        backup_restore.restore(backup, source, stopped=True)
    assert backup_restore.inventory(source) == inventory

    # Same in-process release, new data path AND origin; never reuse the old server URL.
    with _serve(restored, previous_port=original_port) as (client, new_port):
        assert new_port != original_port
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["version"] == __version__
        _health_and_dashboard(client)
        refreshed = _owned_task(client, task_path, owner, foreign)
        assert _media(client, refreshed, owner, foreign) == media
        old_parts = urlsplit(signed)
        new_origin = urlsplit(str(client.base_url))
        transplanted = urlunsplit(
            (new_origin.scheme, new_origin.netloc, old_parts.path, old_parts.query, "")
        )
        # Original signature/expiry must survive, not just newly refreshed links.
        assert urlsplit(transplanted).query == old_parts.query
        assert _media(client, transplanted, owner, foreign) == media
    assert backup_restore.inventory(source) == inventory
    assert backup_restore.inventory(backup / "payload") == inventory

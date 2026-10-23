from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from muse_console.__main__ import dashboard_url, startup_banner
from muse_console.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in os.environ:
        if name.upper().startswith(("MUSE_CONSOLE_", "MUSE2API_")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_CONSOLE_DRIVER", "mock")
    monkeypatch.setenv("MUSE_CONSOLE_KEEPALIVE_ENABLED", "false")


# Exercise the real module entry point and app factory in a fresh interpreter.
# Only the network server is replaced: TestClient runs startup/shutdown and auth
# without binding a port. This is not a Uvicorn/socket or installed-script test.
_CLI_PROBE = """
import os
import runpy
import socket

import uvicorn
from fastapi.testclient import TestClient


original_connect = socket.socket.connect


def local_connect(sock, address):
    # Windows asyncio uses a loopback socketpair for its internal wakeup pipe.
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        assert address[0] in ("127.0.0.1", "::1"), "External network is forbidden"
    return original_connect(sock, address)


def no_network(*args, **kwargs):
    raise AssertionError("CLI startup must stay offline")


socket.socket.connect = local_connect
socket.create_connection = no_network


def serve(app, *, host, port, log_level):
    settings = app.state.services.settings
    assert settings.driver == "mock"
    assert app.state.services.driver.name == "mock"
    assert not settings.keepalive_enabled
    assert host == "127.0.0.1"
    assert port == settings.port
    assert log_level == settings.log_level.lower()
    configured = os.environ.get("MUSE_CONSOLE_API_KEY", "")
    if configured:
        assert settings.api_key == configured
        assert not settings.key_file.exists()
    else:
        assert settings.key_file.read_text(encoding="utf-8").strip() == settings.api_key
    with TestClient(app) as client:
        assert client.get("/healthz").json()["ok"] is True
        assert client.get("/admin/accounts").status_code == 401
        response = client.get(
            "/admin/accounts",
            headers={"Authorization": f"Bearer {settings.effective_admin_key}"},
        )
        assert response.status_code == 200
        assert response.json()["data"] == []
    print("CLI lifecycle verified", flush=True)


uvicorn.run = serve
runpy.run_module("muse_console", run_name="__main__")
"""


def _run_cli(tmp_path, *, api_key="", admin_key=""):
    # Allowlist OS essentials, not inherited app settings, credentials or proxies.
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    env.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        MUSE_CONSOLE_DRIVER="mock",
        MUSE_CONSOLE_KEEPALIVE_ENABLED="false",
        MUSE_CONSOLE_DATA_DIR=str(tmp_path / "data"),
        MUSE_CONSOLE_API_KEY=api_key,
        MUSE_CONSOLE_ADMIN_KEY=admin_key,
    )
    result = subprocess.run(
        [sys.executable, "-c", _CLI_PROBE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CLI lifecycle verified" in result.stdout
    assert "Console: http://127.0.0.1:18610/dashboard" in result.stdout
    assert "driver=mock" in result.stdout
    return result


def _settings(tmp_path, **kw) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", **kw)


def test_dashboard_url_maps_wildcard_host_to_loopback(tmp_path):
    assert dashboard_url(_settings(tmp_path, host="0.0.0.0")) == "http://127.0.0.1:18610/dashboard"
    assert dashboard_url(_settings(tmp_path, host="::1", port=9)) == "http://[::1]:9/dashboard"
    s = _settings(tmp_path, public_base="https://api.example.com/")
    assert dashboard_url(s) == "https://api.example.com/dashboard"


def test_banner_prints_generated_key_only_on_first_start(tmp_path):
    s = _settings(tmp_path)
    key = s.resolve_api_key()
    first = startup_banner(s, key_was_generated=True)
    assert key in first and "/dashboard" in first
    later = startup_banner(s, key_was_generated=False)
    assert key not in later
    assert str(s.key_file) in later


def test_banner_never_prints_configured_keys(tmp_path):
    s = _settings(tmp_path, api_key="configured-secret", admin_key="admin-secret")
    text = startup_banner(s, key_was_generated=False)
    assert "configured-secret" not in text and "admin-secret" not in text


def test_cli_first_start_persists_key_and_restart_does_not_print_it(tmp_path):
    key_file = tmp_path / "data" / "api_key"
    assert not key_file.exists()

    first = _run_cli(tmp_path)
    stored = key_file.read_bytes()
    key = stored.decode("utf-8").strip()
    assert key.startswith("m2a-") and len(key) > len("m2a-")
    assert "First start: generated an API key" in first.stdout
    assert first.stdout.splitlines().count(f"  {key}") == 1
    assert key not in first.stderr
    assert f"Saved to {key_file}. It is not printed again." in first.stdout

    restarted = _run_cli(tmp_path)
    assert key_file.read_bytes() == stored
    assert key not in restarted.stdout + restarted.stderr
    assert "First start:" not in restarted.stdout
    assert f"API/admin key is stored in {key_file}" in restarted.stdout


@pytest.mark.parametrize("admin_key", ["", "synthetic-cli-admin-key"])
def test_cli_configured_keys_are_never_printed(tmp_path, admin_key):
    api_key = "synthetic-cli-api-key"
    for _ in range(2):
        result = _run_cli(tmp_path, api_key=api_key, admin_key=admin_key)
        output = result.stdout + result.stderr
        assert api_key not in output
        if admin_key:
            assert admin_key not in output
        assert "First start:" not in output
        assert not (tmp_path / "data" / "api_key").exists()

"""Offline port preflight regressions; no Chromium or DevTools connections."""

import errno
import socket
import sys
from types import SimpleNamespace

import pytest

from muse_console.drivers.browser import chromium


@pytest.fixture
def fake_launch(tmp_path, monkeypatch):
    process = chromium.ChromiumProcess("not-a-browser", 0, tmp_path / "owned")
    calls = []

    class OwnedProcess:
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def launch(*args, **kwargs):
        calls.append(args)
        return OwnedProcess()

    async def endpoint():
        return f"ws://127.0.0.1:{process.port}/devtools/browser/offline"

    monkeypatch.setattr(chromium.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(process, "browser_ws_url", endpoint)
    return process, calls


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX TIME_WAIT reuse semantics")
async def test_time_wait_allows_owned_launch(fake_launch):
    process, calls = fake_launch
    # The accepted/server side actively closes, leaving its listening port in
    # TIME_WAIT. Both old and new sockets opt into POSIX address reuse.
    with socket.socket() as listener, socket.socket() as client:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(2)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()
        client.settimeout(2)
        client.connect(address)
        with listener.accept()[0] as accepted:
            accepted.settimeout(2)
            accepted.shutdown(socket.SHUT_WR)
            assert client.recv(1) == b""
            client.close()
            assert accepted.recv(1) == b""
    process.port = address[1]
    # Prove the original bind-only preflight actually fails on this fixture.
    with socket.socket() as legacy:
        with pytest.raises(OSError) as error:
            legacy.bind(address)
        assert error.value.errno == errno.EADDRINUSE
    try:
        assert await process.start() == (f"ws://127.0.0.1:{process.port}/devtools/browser/offline")
        assert len(calls) == 1
    finally:
        await process.stop()


@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0"])
@pytest.mark.parametrize("reuse", [False, True])
async def test_live_listener_is_rejected(fake_launch, address, reuse):
    process, calls = fake_launch
    with socket.socket() as listener:
        if reuse:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((address, 0))
        listener.listen(1)
        process.port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match="debugging port is already in use"):
            await process.start()
    assert calls == []
    assert process.proc is None
    assert not process.profile_dir.exists()


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("failure", [None, "bind", "listen"])
async def test_platform_socket_options_and_fail_closed(fake_launch, monkeypatch, platform, failure):
    if platform == "win32" and failure == "listen":
        pytest.skip("Windows exclusive bind does not need listen")
    process, calls = fake_launch
    operations = []
    exclusive = 0x7FFF

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            operations.append("close")

        def setsockopt(self, level, option, value):
            operations.append((level, option, value))

        def bind(self, address):
            operations.append(("bind", address))
            if failure == "bind":
                raise OSError(errno.EADDRINUSE, "occupied")

        def listen(self, backlog):
            operations.append(("listen", backlog))
            if failure == "listen":
                raise OSError(errno.EADDRINUSE, "wildcard listener")

    monkeypatch.setattr(chromium, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        chromium,
        "socket",
        SimpleNamespace(
            AF_INET=socket.AF_INET,
            SOCK_STREAM=socket.SOCK_STREAM,
            SOL_SOCKET=socket.SOL_SOCKET,
            SO_REUSEADDR=socket.SO_REUSEADDR,
            SO_EXCLUSIVEADDRUSE=exclusive,
            socket=lambda *args: Probe(),
        ),
    )
    if failure:
        with pytest.raises(RuntimeError, match="already in use"):
            await process.start()
        assert calls == []
        assert not process.profile_dir.exists()
    else:
        try:
            await process.start()
            assert len(calls) == 1
        finally:
            await process.stop()
    option = exclusive if platform == "win32" else socket.SO_REUSEADDR
    host = "0.0.0.0" if platform == "win32" else "127.0.0.1"
    expected = [(socket.SOL_SOCKET, option, 1), ("bind", (host, 0))]
    if platform != "win32" and failure != "bind":
        expected.append(("listen", 1))
    assert operations == [*expected, "close"]


@pytest.mark.parametrize("lock", ["SingletonLock", "SingletonSocket", "SingletonCookie"])
async def test_stop_does_not_remove_singleton_or_allow_restart(fake_launch, lock):
    process, calls = fake_launch
    await process.start()
    marker = process.profile_dir / lock
    marker.write_bytes(b"untouched")
    await process.stop()
    assert marker.read_bytes() == b"untouched"
    with pytest.raises(RuntimeError, match="refusing to remove browser locks"):
        await process.start()
    assert marker.read_bytes() == b"untouched"
    assert len(calls) == 1

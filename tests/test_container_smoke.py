"""Offline regression coverage; no Docker daemon or upstream access."""

import json

import pytest
from tools import container_smoke as smoke

MARKERS = (
    "INFO:     Shutting down\n"
    "INFO:     Application shutdown complete.\n"
    "INFO:     Finished server process [9]\n"
)
LOCKS = {name: False for name in ("SingletonLock", "SingletonSocket", "SingletonCookie")}


def state(exit_code=143, **changes):
    return {
        "Running": False,
        "Status": "exited",
        "Dead": False,
        "OOMKilled": False,
        "ExitCode": exit_code,
        "StartedAt": "2026-10-23T00:00:00.000000000Z",
        **changes,
    }


@pytest.mark.parametrize("exit_code", [0, 143])
def test_clean_stop_accepts_only_evidenced_shutdown(monkeypatch, exit_code):
    calls = []

    def docker(*args):
        calls.append(args)
        return MARKERS.encode()

    monkeypatch.setattr(smoke, "docker", docker)
    smoke.check_clean_stop("test", state(exit_code))
    assert calls == [("logs", "--since", state()["StartedAt"], "test")]


@pytest.mark.parametrize(
    "changes",
    [
        {"ExitCode": 137},
        {"ExitCode": 3},
        {"ExitCode": False},
        {"OOMKilled": True},
        {"Dead": True},
        {"Running": True},
        {"Status": "dead"},
    ],
)
def test_stop_rejects_bad_state(monkeypatch, changes):
    monkeypatch.setattr(smoke, "docker", lambda *a: MARKERS.encode())
    with pytest.raises(smoke.SmokeFailure, match="GRACEFUL_STOP_NOT_CLEAN"):
        smoke.check_clean_stop("test", state(**changes))


@pytest.mark.parametrize("exit_code", [0, 143])
@pytest.mark.parametrize("missing", MARKERS.splitlines())
def test_stop_requires_every_marker(monkeypatch, exit_code, missing):
    logs = MARKERS.replace(missing + "\n", "")
    monkeypatch.setattr(smoke, "docker", lambda *a: logs.encode())
    with pytest.raises(smoke.SmokeFailure, match="GRACEFUL_STOP_NOT_CLEAN"):
        smoke.check_clean_stop("test", state(exit_code))


@pytest.mark.parametrize(
    "error",
    [
        "ERROR:    Application shutdown failed. Exiting.",
        "Chromium profile is locked; refusing to remove browser locks",
        "Chromium did not expose a DevTools endpoint",
    ],
)
def test_stop_rejects_failure_logs_without_leaking(monkeypatch, capsys, error):
    secret = "synthetic-private-key-and-path"
    monkeypatch.setattr(smoke, "docker", lambda *a: (MARKERS + error + secret).encode())
    with pytest.raises(smoke.SmokeFailure, match="GRACEFUL_STOP_NOT_CLEAN"):
        smoke.check_clean_stop("test", state())
    assert secret not in capsys.readouterr().out


@pytest.mark.parametrize("lock", [None, *LOCKS])
def test_readonly_lock_helper(monkeypatch, lock):
    calls = []
    result = {**LOCKS, **({lock: True} if lock else {})}

    def docker(*args):
        calls.append(args)
        return json.dumps(result).encode()

    monkeypatch.setattr(smoke, "docker", docker)
    if lock:
        with pytest.raises(smoke.SmokeFailure, match="PROFILE_LOCKS_REMAIN"):
            smoke.check_profile_unlocked("same:image", "temp-volume", "helper")
        assert len(calls) == 1
    else:
        smoke.check_profile_unlocked("same:image", "temp-volume", "helper")
        assert calls[-1] == ("rm", "helper")
    args = calls[0]
    assert args[:5] == ("run", "--name", "helper", "--network", "none")
    assert "--read-only" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert (
        args[args.index("--mount") + 1]
        == "type=volume,source=temp-volume,target=/app/data,readonly"
    )
    assert args[args.index("--entrypoint") + 1 :] == (
        "python",
        "same:image",
        "-c",
        smoke.LOCK_PROBE,
    )
    assert "--user" not in args  # preserve image's non-root default, checked by probe


@pytest.mark.parametrize("result", [{}, {**LOCKS, "SingletonLock": 0}, {**LOCKS, "private": False}])
def test_lock_probe_fails_closed(monkeypatch, result):
    monkeypatch.setattr(smoke, "docker", lambda *a: json.dumps(result).encode())
    with pytest.raises(smoke.SmokeFailure, match="PROFILE_LOCK_PROBE_INVALID"):
        smoke.check_profile_unlocked("same:image", "temp-volume", "helper")


@pytest.mark.parametrize("remaining_lock", [False, True])
def test_recreate_order_and_cleanup(monkeypatch, remaining_lock):
    calls = []

    def docker(*args):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Architecture": "arm64", "Os": "linux"}]).encode()
        if args[0] == "logs":
            return MARKERS.encode()
        if args[0] == "run" and "--entrypoint" in args:
            return json.dumps({**LOCKS, "SingletonLock": remaining_lock}).encode()
        return b""

    monkeypatch.setattr(smoke, "docker", docker)
    monkeypatch.setattr(smoke, "inspect_container", lambda *a: {"State": state()})
    monkeypatch.setattr(smoke, "check_isolation", lambda *a: "timestamp")
    monkeypatch.setattr(smoke, "wait_ready", lambda *a: "a" * 64)
    monkeypatch.setattr(smoke, "check_logs", lambda *a, **kw: None)
    if remaining_lock:
        with pytest.raises(smoke.SmokeFailure, match="PROFILE_LOCKS_REMAIN"):
            smoke.run_smoke("same:image", "arm64")
    else:
        smoke.run_smoke("same:image", "arm64")
    stop = next(i for i, args in enumerate(calls) if args[0] == "stop")
    assert calls[stop][1:3] == ("--time", "20")
    assert calls[stop + 1][0] == "logs"
    helper_run = calls[stop + 2]
    assert "--entrypoint" in helper_run
    service_runs = [args for args in calls if args[0] == "run" and "--entrypoint" not in args]
    if remaining_lock:
        assert len(service_runs) == 1
        assert calls[stop + 3] == ("rm", "--force", helper_run[2])
    else:
        assert calls[stop + 3] == ("rm", helper_run[2])
        assert calls[stop + 4] == ("rm", calls[stop][-1])
        assert calls[stop + 5] == service_runs[1]
        assert service_runs[0] == service_runs[1]
    assert calls[-2][:2] == ("rm", "--force")
    assert calls[-1][:2] == ("volume", "rm")

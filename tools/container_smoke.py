"""Dependency-free, offline Docker smoke; never print keys or raw container logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import subprocess
import sys
import textwrap
import time
import uuid

# Executed inside the isolated container. Only a one-way fingerprint leaves it.
PROBE_BODY = r"""
stage = "CONFIG"
import hashlib
import json
import os
from pathlib import Path
import urllib.request
from muse_console.config import Settings

s = Settings()
assert s.driver == "browser" and s.chromium_no_sandbox is True
assert s.host == "0.0.0.0" and s.keepalive_enabled is False
assert not s.api_key and not s.admin_key
stage = "PROCESS_SECURITY"
assert os.geteuid() != 0
status = dict(line.split(":", 1) for line in Path("/proc/1/status").read_text().splitlines())
assert all(int(uid) != 0 for uid in status["Uid"].split())
assert int(status["CapEff"].strip(), 16) == 0
assert status["NoNewPrivs"].strip() == "1"
assert not Path("/var/run/docker.sock").exists()

stage = "HTTP_HEALTH_READY"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = "http://127.0.0.1:18610"
def get(path):
    return opener.open(base + path, timeout=4)
with get("/healthz") as r:
    assert r.status == 200 and json.load(r)["ok"] is True
with get("/readyz") as r:
    ready = json.load(r)
assert ready["driver"]["driver"] == "browser" and ready["driver"]["ok"] is True
# Browser requires an account to serve requests; this test must never add one.
assert ready["accounts"]["total"] == 0
assert ready["accounts"]["available"] == 0 and ready["ready"] is False
stage = "CONSOLE"
with get("/dashboard") as r:
    assert r.status == 200 and "text/html" in r.headers["Content-Type"]
    assert b"<html" in r.read().lower()

stage = "KEY_AUTH"
key = s.key_file.read_text().strip()
assert key.startswith("m2a-") and len(key) >= 32
assert s.key_file.stat().st_mode & 0o777 == 0o600
request = urllib.request.Request(base + "/admin/status", headers={"Authorization": "Bearer " + key})
with opener.open(request, timeout=4) as r:
    assert r.status == 200

stage = "BROWSER_ARGS"
# Verify the real browser command line, not just environment defaults.
browsers = []
for path in Path("/proc").glob("[0-9]*/cmdline"):
    try:
        args = path.read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    if b"--remote-debugging-port=19210" in args:
        browsers.append(args)
assert browsers
for args in browsers:
    assert b"--no-sandbox" in args
    assert b"--remote-debugging-address=127.0.0.1" in args
    assert b"about:blank" in args

stage = "LISTENERS"
# Match the image/compose default: HTTP wildcard inside, CDP loopback only.
# No host ports are published in this disconnected smoke.
listeners = {18610: [], 19210: []}
for table in ("/proc/net/tcp", "/proc/net/tcp6"):
    for row in Path(table).read_text().splitlines()[1:]:
        fields = row.split()
        address, port = fields[1].split(":")
        port = int(port, 16)
        if fields[3] == "0A" and port in listeners:
            listeners[port].append(address)
assert listeners[18610] == ["00000000"]
assert listeners[19210] and all(
    a in ("0100007F", "00000000000000000000000001000000")
    for a in listeners[19210]
)
print(json.dumps({"fingerprint": hashlib.sha256(key.encode()).hexdigest()}))
"""


PROBE_CODES = {
    "CONFIG",
    "PROCESS_SECURITY",
    "HTTP_HEALTH_READY",
    "CONSOLE",
    "KEY_AUTH",
    "BROWSER_ARGS",
    "LISTENERS",
}
# Return only a fixed diagnostic category, never exception text or locals.
PROBE = (
    "import json\ntry:\n"
    + textwrap.indent(PROBE_BODY, "    ")
    + '\nexcept Exception:\n    print(json.dumps({"code": stage}))\n'
)


class SmokeFailure(Exception):
    """A deliberately redacted failure safe for CI output."""


def docker(*args: str, timeout: int = 60) -> bytes:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SmokeFailure("DOCKER_UNAVAILABLE_OR_TIMEOUT") from None
    if result.returncode:
        # Neither stderr nor CalledProcessError is safe: they can contain logs/keys.
        raise SmokeFailure("DOCKER_COMMAND_FAILED")
    return result.stdout + result.stderr if args[0] == "logs" else result.stdout


def inspect_container(name: str) -> dict:
    return json.loads(docker("inspect", name))[0]


def check_isolation(name: str, volume: str) -> str:
    info = inspect_container(name)
    host = info["HostConfig"]
    assert host["NetworkMode"] == "none"
    assert host["CapDrop"] == ["ALL"] and not host.get("CapAdd")
    assert "no-new-privileges" in host["SecurityOpt"]
    assert not host["Privileged"] and not host.get("PortBindings")
    assert host["Init"] is True
    assert host["Memory"] == 4 * 1024**3 and host["PidsLimit"] == 512
    mounts = info["Mounts"]
    assert len(mounts) == 1
    assert mounts[0]["Type"] == "volume" and mounts[0]["Name"] == volume
    assert mounts[0]["Destination"] == "/app/data"
    return info["State"]["StartedAt"]


def wait_ready(name: str) -> str:
    deadline = time.monotonic() + 120
    code = "PROBE_NO_RESULT"
    while time.monotonic() < deadline:
        try:
            result = json.loads(docker("exec", name, "python", "-c", PROBE, timeout=15))
            if "code" in result:
                code = result["code"] if result["code"] in PROBE_CODES else "PROBE_INVALID_RESULT"
                time.sleep(2)
                continue
            fingerprint = result["fingerprint"]
            assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            return fingerprint
        except SmokeFailure:
            code = "PROBE_EXEC_FAILED"
            time.sleep(2)
        except (ValueError, KeyError, AssertionError, TypeError):
            code = "PROBE_INVALID_RESULT"
            time.sleep(2)
    raise SmokeFailure("PROBE_TIMEOUT_" + code)


def check_logs(name: str, since: str, fingerprint: str, *, first: bool) -> None:
    # Captured only in memory: never written to a file, artifact, or CI console.
    logs = docker("logs", "--since", since, name).decode("utf-8", errors="replace")
    marker = "First start: generated an API key"
    if "Console: http://127.0.0.1:18610/dashboard" not in logs:
        raise SmokeFailure("LOG_CONSOLE_MISSING")
    tokens = re.findall(r"m2a-[A-Za-z0-9_-]+", logs)
    if first:
        if logs.count(marker) != 1 or len(tokens) != 1:
            raise SmokeFailure("LOG_FIRST_KEY_COUNT")
        if hashlib.sha256(tokens[0].encode()).hexdigest() != fingerprint:
            raise SmokeFailure("LOG_FIRST_KEY_MISMATCH")
    else:
        if marker in logs or tokens:
            raise SmokeFailure("LOG_KEY_REPRINTED")


def run_smoke(image: str, arch: str) -> None:
    suffix = uuid.uuid4().hex
    name = "muse-smoke-" + suffix
    volume = "muse-smoke-data-" + suffix
    volume_created = False
    stage = "image.inspect"
    try:
        metadata = json.loads(docker("image", "inspect", image))[0]
        assert metadata["Architecture"] == arch and metadata["Os"] == "linux"
        stage = "volume.create"
        docker("volume", "create", volume)
        volume_created = True
        fingerprint = None
        for phase in ("first", "restart", "recreate"):
            stage = phase + ".start"
            if phase == "restart":
                docker("restart", "--time", "20", name)
            else:
                if phase == "recreate":
                    docker("rm", "--force", name)
                docker(
                    "run",
                    "--detach",
                    "--init",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--shm-size",
                    "1g",
                    "--memory",
                    "4g",
                    "--pids-limit",
                    "512",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--mount",
                    f"type=volume,source={volume},target=/app/data",
                    "--env",
                    "MUSE_CONSOLE_KEEPALIVE_ENABLED=false",
                    image,
                )
            stage = phase + ".isolation"
            since = check_isolation(name, volume)
            stage = phase + ".probe"
            current = wait_ready(name)
            if fingerprint is None:
                fingerprint = current
            if current != fingerprint:
                raise SmokeFailure("KEY_CHANGED")
            stage = phase + ".logs"
            check_logs(name, since, fingerprint, first=phase == "first")
            print(f"PASS: {arch} {phase} (offline browser, isolation, HTTP, key lifecycle)")
    except SmokeFailure as error:
        print(f"FAIL: stage={stage} code={error}", file=sys.stderr)
        raise
    except (Exception, KeyboardInterrupt):
        print(f"FAIL: stage={stage} code=CHECK_FAILED_OR_INTERRUPTED", file=sys.stderr)
        raise
    finally:
        # Attempt both operations even if the first cleanup fails; report leaks.
        cleaned = True
        for args in (("rm", "--force", name), ("volume", "rm", volume)):
            if args[0] == "volume" and not volume_created:
                continue
            try:
                docker(*args)
            except SmokeFailure:
                cleaned = False
        if not cleaned:
            print("FAIL: stage=cleanup code=RESOURCE_REMOVAL_FAILED", file=sys.stderr)
            raise SmokeFailure("RESOURCE_REMOVAL_FAILED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    args = parser.parse_args()

    def cancelled(signum: int, frame: object) -> None:
        raise SmokeFailure("CANCELLED")

    signal.signal(signal.SIGTERM, cancelled)
    try:
        run_smoke(args.image, args.arch)
    except (Exception, KeyboardInterrupt):
        # No traceback, exception text, command output, key or fingerprint escapes.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

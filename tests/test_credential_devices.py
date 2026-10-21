import hashlib
import sqlite3
import time

import pytest

from muse_console.services.credential_devices import CredentialBoundaryError, CredentialDevices


async def test_pair_once_rotate_revoke_persist(tmp_path):
    path = tmp_path / "devices.db"
    devices = CredentialDevices(path)
    await devices.startup()
    server = devices.server()
    challenge = devices.challenge()
    grant = devices.pair(challenge["challenge"], server["server_id"])
    with pytest.raises(CredentialBoundaryError):
        devices.pair(challenge["challenge"], server["server_id"])
    assert devices.authenticate(grant["token"]) == grant["device_id"]
    rotated = devices.rotate(grant["device_id"])
    with pytest.raises(CredentialBoundaryError):
        devices.authenticate(grant["token"])
    await devices.close()
    devices = CredentialDevices(path)
    await devices.startup()
    assert devices.server() == server
    assert devices.authenticate(rotated["token"]) == grant["device_id"]
    devices.revoke(grant["device_id"])
    with pytest.raises(CredentialBoundaryError):
        devices.authenticate(rotated["token"])
    assert "token_hash" not in str(devices.list())
    await devices.close()
    raw = path.read_bytes()
    assert rotated["token"].encode() not in raw
    assert challenge["challenge"].encode() not in raw


async def test_pre_migration_device_hash_remains_valid(tmp_path):
    """Persist an old unsalted digest independently of the implementation helper."""
    path = tmp_path / "old-devices.db"
    token = "existing-device-token"
    devices = CredentialDevices(path)
    await devices.startup()
    with devices.db:
        devices.db.execute(
            "INSERT INTO devices VALUES (?,?,?,?,?,?)",
            (
                "legacy-device",
                hashlib.sha256(token.encode()).hexdigest(),
                time.time() + 3600,
                0,
                1,
                "old installation",
            ),
        )
    await devices.close()
    devices = CredentialDevices(path)
    await devices.startup()
    assert devices.authenticate(token) == "legacy-device"
    await devices.close()


async def test_legacy_confirmation_migration_is_fail_closed(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE confirmations (device TEXT,capture TEXT,account TEXT,PRIMARY KEY(device,capture))"
        )
        db.execute("INSERT INTO confirmations VALUES ('device','capture_legacy','account')")
        db.execute(
            "CREATE TABLE captures (device TEXT,id TEXT,fingerprint TEXT,source TEXT,revision INTEGER,result TEXT,PRIMARY KEY(device,id))"
        )
        db.execute("INSERT INTO captures VALUES ('device','capture_legacy','fp','source',1,'{}')")
    devices = CredentialDevices(path)
    await devices.startup()
    row = devices.db.execute("SELECT * FROM confirmations").fetchone()
    assert row["version"] is None
    assert devices.confirmation("device", "capture_legacy") is None
    await devices.close()


async def test_ttl_limit_rate(tmp_path):
    devices = CredentialDevices(tmp_path / "devices.db", device_limit=1)
    await devices.startup()
    challenge = devices.challenge()
    grant = devices.pair(challenge["challenge"], challenge["server_id"])
    challenge = devices.challenge()
    with pytest.raises(CredentialBoundaryError, match="device_limit"):
        devices.pair(challenge["challenge"], challenge["server_id"])
    with devices.db:
        devices.db.execute("UPDATE devices SET expires=?", (time.time() - 1,))
    with pytest.raises(CredentialBoundaryError):
        devices.authenticate(grant["token"])
    with devices.db:
        devices.db.execute("UPDATE challenges SET expires=?", (time.time() - 1,))
    with pytest.raises(CredentialBoundaryError, match="invalid_challenge"):
        devices.pair(challenge["challenge"], challenge["server_id"])
    devices.rate("test", limit=1)
    with pytest.raises(CredentialBoundaryError, match="rate_limited"):
        devices.rate("test", limit=1)
    await devices.close()

"""Persistent restricted grants and non-secret ingestion receipts (single-process service)."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path


class CredentialBoundaryError(Exception):
    """Only fixed, non-secret codes may cross the API boundary."""

    def __init__(self, code: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class CredentialDevices:
    def __init__(self, db_path: Path | str, *, device_limit: int = 16) -> None:
        self.path = Path(db_path)
        self.device_limit = device_limit
        self.db: sqlite3.Connection | None = None

    async def startup(self) -> None:
        if self.db is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS identity (id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS challenges (
                hash TEXT PRIMARY KEY, expires REAL, quota INTEGER, ttl REAL, label TEXT);
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, expires REAL,
                revoked INTEGER DEFAULT 0, quota INTEGER, label TEXT);
            CREATE TABLE IF NOT EXISTS captures (
                device TEXT, id TEXT, fingerprint TEXT, source TEXT, revision INTEGER,
                result TEXT, PRIMARY KEY(device,id));
            CREATE TABLE IF NOT EXISTS bindings (
                source TEXT PRIMARY KEY, device TEXT, account TEXT, revision INTEGER,
                fingerprint TEXT, created INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS confirmations (
                device TEXT, capture TEXT, account TEXT, PRIMARY KEY(device,capture));
            CREATE TABLE IF NOT EXISTS rates (key TEXT PRIMARY KEY, start REAL, count INTEGER);
            CREATE TABLE IF NOT EXISTS capture_times (
                device TEXT, id TEXT, saved REAL, PRIMARY KEY(device,id));
        """)
        # Existing confirmations deliberately receive NULL version/scope: fail closed.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(confirmations)")}
        for name, kind in (
            ("version", "INTEGER"),
            ("fingerprint", "TEXT"),
            ("source", "TEXT"),
            ("revision", "INTEGER"),
        ):
            if name not in columns:
                self.db.execute(f"ALTER TABLE confirmations ADD COLUMN {name} {kind}")
        if not self.db.execute("SELECT id FROM identity").fetchone():
            self.db.execute("INSERT INTO identity VALUES (?)", ("srv_" + secrets.token_hex(16),))
        self.db.execute(
            "INSERT OR IGNORE INTO capture_times SELECT device,id,? FROM captures", (time.time(),)
        )
        self.db.commit()
        self.prune()

    async def close(self) -> None:
        if self.db:
            self.db.close()
            self.db = None

    @property
    def server_id(self) -> str:
        return self.db.execute("SELECT id FROM identity").fetchone()[0]

    def server(self) -> dict:
        # An identifier checksum, explicitly not a TLS certificate pin.
        return {"server_id": self.server_id, "fingerprint": digest(self.server_id)}

    def prune(self) -> None:
        """Bound receipts to 30 days / 10k entries; bindings retain revision high-water.

        After receipt expiry, old snapshots cannot mutate an existing source again.
        A missing receipt is not a new-account grant; quota/bindings remain durable.
        """
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM rates WHERE start<?", (now - 3600,))
            self.db.execute("DELETE FROM challenges WHERE expires<=?", (now,))
            self.db.execute("DELETE FROM capture_times WHERE saved<?", (now - 30 * 86400,))
            self.db.execute(
                "DELETE FROM capture_times WHERE rowid NOT IN "
                "(SELECT rowid FROM capture_times ORDER BY saved DESC LIMIT 10000)"
            )
            self.db.execute(
                "DELETE FROM captures WHERE NOT EXISTS (SELECT 1 FROM capture_times t "
                "WHERE t.device=captures.device AND t.id=captures.id)"
            )
            self.db.execute(
                "DELETE FROM confirmations WHERE NOT EXISTS (SELECT 1 FROM captures c "
                "WHERE c.device=confirmations.device AND c.id=confirmations.capture)"
            )

    def touch_capture(self, device: str, capture: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO capture_times VALUES (?,?,?)", (device, capture, time.time())
            )

    def rate(self, key: str, *, limit: int, window: float = 60) -> None:
        self.prune()
        now = time.time()
        row = self.db.execute("SELECT * FROM rates WHERE key=?", (key,)).fetchone()
        start, count = (row["start"], row["count"]) if row else (now, 0)
        if now - start >= window:
            start, count = now, 0
        if count >= limit:
            raise CredentialBoundaryError("rate_limited", 429)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO rates VALUES (?,?,?)", (key, start, count + 1))

    def challenge(
        self,
        *,
        ttl: int = 300,
        new_account_quota: int = 1,
        device_ttl: int = 2592000,
        label: str = "",
    ) -> dict:
        if (
            not 30 <= ttl <= 600
            or not 0 <= new_account_quota <= 10
            or not 60 <= device_ttl <= 7776000
        ):
            raise CredentialBoundaryError("invalid_grant_options")
        value = secrets.token_urlsafe(32)
        expires = time.time() + ttl
        with self.db:
            self.db.execute("DELETE FROM challenges WHERE expires<=?", (time.time(),))
            self.db.execute(
                "INSERT INTO challenges VALUES (?,?,?,?,?)",
                (digest(value), expires, new_account_quota, device_ttl, label),
            )
        return {"challenge": value, "server_id": self.server_id, "expires_at": expires}

    def pair(self, challenge: str, server_id: str) -> dict:
        self.rate("pair", limit=20)
        now = time.time()
        with self.db:
            row = self.db.execute(
                "SELECT * FROM challenges WHERE hash=?", (digest(challenge),)
            ).fetchone()
            if server_id != self.server_id or not row or row["expires"] <= now:
                raise CredentialBoundaryError("invalid_challenge", 401)
            count = self.db.execute(
                "SELECT count(*) FROM devices WHERE revoked=0 AND expires>?", (now,)
            ).fetchone()[0]
            if count >= self.device_limit:
                raise CredentialBoundaryError("device_limit", 409)
            device_id, token = "dev_" + secrets.token_hex(16), secrets.token_urlsafe(48)
            expires = now + row["ttl"]
            self.db.execute("DELETE FROM challenges WHERE hash=?", (digest(challenge),))
            self.db.execute(
                "INSERT INTO devices(id,token_hash,expires,quota,label) VALUES (?,?,?,?,?)",
                (device_id, digest(token), expires, row["quota"], row["label"]),
            )
        return {
            "device_id": device_id,
            "token": token,
            "server_id": self.server_id,
            "expires_at": expires,
        }

    def authenticate(self, token: str) -> str:
        row = self.db.execute(
            "SELECT * FROM devices WHERE token_hash=?", (digest(token),)
        ).fetchone()
        if not token or not row or row["revoked"] or row["expires"] <= time.time():
            raise CredentialBoundaryError("invalid_device_token", 401)
        return row["id"]

    def active(self, device: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM devices WHERE id=?", (device,)).fetchone()
        if not row or row["revoked"] or row["expires"] <= time.time():
            raise CredentialBoundaryError("invalid_device_token", 401)
        return row

    def rotate(self, device: str) -> dict:
        self.rate("rotate:" + device, limit=10)
        row = self.active(device)
        token = secrets.token_urlsafe(48)
        with self.db:
            self.db.execute("UPDATE devices SET token_hash=? WHERE id=?", (digest(token), device))
        return {
            "device_id": device,
            "token": token,
            "server_id": self.server_id,
            "expires_at": row["expires"],
        }

    def list(self) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id,label,expires AS expires_at,revoked,quota AS new_account_limit FROM devices"
            )
        ]

    def revoke(self, device: str) -> dict:
        with self.db:
            self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device,))
        return {"revoked": True}

    def capture(self, device: str, capture: str):
        row = self.db.execute(
            "SELECT * FROM captures WHERE device=? AND id=?", (device, capture)
        ).fetchone()
        return dict(row) if row else None

    def receipt(self, device: str, capture: str) -> dict:
        row = self.capture(device, capture)
        if not row:
            raise CredentialBoundaryError("capture_not_found", 404)
        return json.loads(row["result"])

    def save_receipt(self, envelope, source: str, result: dict) -> None:
        self.touch_capture(envelope.device_id, envelope.capture_id)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO captures VALUES (?,?,?,?,?,?)",
                (
                    envelope.device_id,
                    envelope.capture_id,
                    envelope.fingerprint(),
                    source,
                    envelope.source_revision,
                    json.dumps(result),
                ),
            )

    def binding(self, source: str):
        row = self.db.execute("SELECT * FROM bindings WHERE source=?", (source,)).fetchone()
        return dict(row) if row else None

    def bind(
        self,
        *,
        source: str,
        device: str,
        account: str,
        revision: int,
        fingerprint: str,
        created: bool = False,
    ) -> None:
        previous = self.binding(source)
        if previous and previous["revision"] > revision:
            return
        created = created or bool(previous and previous["created"])
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO bindings VALUES (?,?,?,?,?,?)",
                (source, device, account, revision, fingerprint, int(created)),
            )

    def remaining(self, device: str) -> int:
        row = self.active(device)
        used = self.db.execute(
            "SELECT count(*) FROM bindings WHERE device=? AND created=1", (device,)
        ).fetchone()[0]
        return max(0, row["quota"] - used)

    def confirm(self, device: str, capture: str, account: str, *, version: int) -> dict:
        self.active(device)
        result = self.receipt(device, capture)
        if result["status"] not in ("needs_identity_confirmation", "conflict"):
            raise CredentialBoundaryError("capture_not_confirmable", 409)
        candidate = self.capture(device, capture)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO confirmations "
                "(device,capture,account,version,fingerprint,source,revision) VALUES (?,?,?,?,?,?,?)",
                (
                    device,
                    capture,
                    account,
                    version,
                    candidate["fingerprint"],
                    candidate["source"],
                    candidate["revision"],
                ),
            )
        return {"confirmed": True, "id": capture, "account_id": account}

    def clear_confirmation(self, device: str, capture: str) -> None:
        with self.db:
            self.db.execute(
                "DELETE FROM confirmations WHERE device=? AND capture=?", (device, capture)
            )

    def rebind(self, source_account: str, target_account: str) -> int:
        """Point device bindings and confirmations at a merged account."""
        if self.db is None or source_account == target_account:
            return 0
        with self.db:
            moved = self.db.execute(
                "UPDATE bindings SET account=? WHERE account=?", (target_account, source_account)
            ).rowcount
            self.db.execute(
                "UPDATE confirmations SET account=? WHERE account=?",
                (target_account, source_account),
            )
        return moved

    def confirmation(self, device: str, capture: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM confirmations WHERE device=? AND capture=?", (device, capture)
        ).fetchone()
        if not row or row["version"] is None:
            return None
        candidate = self.capture(device, capture)
        if not candidate or any(
            row[k] != candidate[k] for k in ("fingerprint", "source", "revision")
        ):
            self.clear_confirmation(device, capture)
            return None
        return dict(row)

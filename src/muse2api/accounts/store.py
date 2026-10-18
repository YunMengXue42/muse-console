"""Account persistence with encrypted SQLite snapshots and legacy JSON compatibility."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from .model import Account


class AccountStore(Protocol):
    async def load(self) -> list[Account]: ...
    async def save(self, accounts: list[Account]) -> None: ...


class JsonAccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def load(self) -> list[Account]:
        if not self.path.is_file():
            return []
        raw = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        if not raw.strip():
            return []
        return [Account.model_validate(item) for item in json.loads(raw)]

    async def save(self, accounts: list[Account]) -> None:
        payload = json.dumps(
            [a.model_dump(mode="json") for a in accounts], ensure_ascii=False, indent=2
        )
        async with self._lock:
            await asyncio.to_thread(self._atomic_write, payload)

    def _atomic_write(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".accounts.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def session_digest(account: Account) -> str | None:
    value = account.cookies.get("hatch_sess")
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def reject_duplicate_sessions(accounts: list[Account]) -> None:
    seen: set[str] = set()
    for account in accounts:
        digest = session_digest(account)
        if digest and digest in seen:
            raise ValueError("duplicate account session")
        if digest:
            seen.add(digest)


class SQLiteAccountStore:
    """Encrypted snapshots; all database and key operations run off the event loop."""

    def __init__(self, path: Path, *, key_path: Path, legacy_path: Path | None = None) -> None:
        self.path = Path(path)
        self.key_path = Path(key_path)
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self._lock = asyncio.Lock()

    def _cipher(self, db: sqlite3.Connection) -> Fernet:
        if not self.key_path.exists():
            if db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
                raise RuntimeError("credentials key missing for non-empty account database")
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
        os.chmod(self.key_path, 0o600)
        if os.name != "nt" and stat.S_IMODE(self.key_path.stat().st_mode) != 0o600:
            raise RuntimeError("credentials key permissions must be 0600")
        try:
            return Fernet(self.key_path.read_bytes())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("invalid credentials key") from exc

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.execute(
            "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, session_hash TEXT, payload BLOB NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS store_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='accounts'").fetchone()[0]
        if "session_hash TEXT UNIQUE" in schema:
            with db:
                db.execute("ALTER TABLE accounts RENAME TO accounts_unique")
                db.execute(
                    "CREATE TABLE accounts (id TEXT PRIMARY KEY, session_hash TEXT, payload BLOB NOT NULL)"
                )
                db.execute("INSERT INTO accounts SELECT * FROM accounts_unique")
                db.execute("DROP TABLE accounts_unique")
        db.commit()
        return db

    def _write(self, db: sqlite3.Connection, cipher: Fernet, accounts: list[Account]) -> None:
        rows = [
            (a.id, session_digest(a), cipher.encrypt(a.model_dump_json().encode()))
            for a in accounts
        ]
        db.execute("DELETE FROM accounts")
        db.executemany("INSERT INTO accounts VALUES (?, ?, ?)", rows)

    def _load(self) -> list[Account]:
        db = self._connect()
        try:
            cipher = self._cipher(db)
            with db:
                # The marker also prevents reimport after intentionally removing every account.
                migrated = db.execute(
                    "SELECT 1 FROM store_meta WHERE name='legacy_checked'"
                ).fetchone()
                if not migrated:
                    empty = not db.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
                    if empty and self.legacy_path and self.legacy_path.is_file():
                        raw = self.legacy_path.read_text(encoding="utf-8")
                        accounts = [
                            Account.model_validate(item) for item in json.loads(raw or "[]")
                        ]
                        self._write(db, cipher, accounts)
                    db.execute("INSERT INTO store_meta VALUES ('legacy_checked', '1')")
            try:
                return [
                    Account.model_validate_json(cipher.decrypt(row[0]))
                    for row in db.execute("SELECT payload FROM accounts ORDER BY rowid")
                ]
            except InvalidToken as exc:
                raise RuntimeError("cannot decrypt account database with credentials key") from exc
        finally:
            db.close()

    def _save(self, accounts: list[Account]) -> None:
        db = self._connect()
        try:
            cipher = self._cipher(db)
            # Validate existing ciphertext before replacing anything with a different key.
            for row in db.execute("SELECT payload FROM accounts"):
                try:
                    cipher.decrypt(row[0])
                except InvalidToken as exc:
                    raise RuntimeError(
                        "cannot decrypt account database with credentials key"
                    ) from exc
            with db:
                self._write(db, cipher, accounts)
                db.execute("INSERT OR IGNORE INTO store_meta VALUES ('legacy_checked', '1')")
        finally:
            db.close()

    async def _run(self, operation, *args):
        async with self._lock:
            task = asyncio.create_task(asyncio.to_thread(operation, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def load(self) -> list[Account]:
        return await self._run(self._load)

    async def save(self, accounts: list[Account]) -> None:
        snapshot = [a.model_copy(deep=True) for a in accounts]
        await self._run(self._save, snapshot)

    def _load_policy(self) -> dict:
        db = self._connect()
        try:
            return {
                row[0]: json.loads(row[1]) for row in db.execute("SELECT name, value FROM settings")
            }
        finally:
            db.close()

    def _save_policy(self, policy: dict) -> None:
        db = self._connect()
        try:
            with db:
                db.executemany(
                    "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                    [(k, json.dumps(v)) for k, v in policy.items()],
                )
        finally:
            db.close()

    async def load_policy(self) -> dict:
        return await self._run(self._load_policy)

    async def save_policy(self, policy: dict) -> None:
        await self._run(self._save_policy, dict(policy))

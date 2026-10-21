"""Local media storage for generated images/videos and helpers for image inputs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import stat
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from ..errors import CapacityExceeded, InvalidRequest, NotFound
from .safe_download import download_image

_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9]+$")
_MANAGED_MEDIA_NAME = re.compile(r"^(?:img|vid)_[0-9a-f]{16}\.[a-z0-9]+$")
log = logging.getLogger(__name__)


class MediaStore:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = 32 * 1024 * 1024,
        storage_max_bytes: int = 1024 * 1024 * 1024,
        url_ttl: int = 3600,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.storage_max_bytes = storage_max_bytes
        self.url_ttl = url_ttl
        self._db_path = root.parent / "media-owners.db"
        key_path = root.parent / "media-signing.key"
        if key_path.is_symlink() or self._db_path.is_symlink():
            raise ValueError("media metadata must not be symlinks")
        if not key_path.exists():
            if self._db_path.exists():
                raise ValueError("media signing key is missing; restore the matching backup")
            with key_path.open("xb") as stream:
                os.chmod(key_path, 0o600)
                stream.write(secrets.token_bytes(32))
        self._signing_key = key_path.read_bytes()
        if len(self._signing_key) != 32:
            raise ValueError("invalid media signing key")
        with sqlite3.connect(self._db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS owners (name TEXT PRIMARY KEY, owner TEXT)")
        os.chmod(self._db_path, 0o600)

    def save(
        self, data: bytes, mime: str, prefix: str = "m", *, owner_key_id: str | None = None
    ) -> str:
        """store bounded media with its submitting key; legacy ownerless files are admin-only."""
        if len(data) > self.max_bytes:
            raise CapacityExceeded("generated media exceeds the file size limit")
        used = sum(
            p.lstat().st_size for p in self.root.iterdir() if not p.is_symlink() and p.is_file()
        )
        if used + len(data) > self.storage_max_bytes:
            raise CapacityExceeded("media storage is full; ask the administrator to free space")
        ext = _EXT_BY_MIME.get(mime.split(";")[0].strip().lower(), ".bin")
        name = f"{prefix}_{uuid.uuid4().hex[:16]}{ext}"
        path = self.root / name
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(data)
        try:
            with sqlite3.connect(self._db_path) as db:
                db.execute("INSERT INTO owners VALUES (?, ?)", (name, owner_key_id))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return name

    def owner_of(self, name: str) -> str | None:
        with sqlite3.connect(self._db_path) as db:
            row = db.execute("SELECT owner FROM owners WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def _signature(self, name: str, owner: str, expires: int) -> str:
        payload = f"{name}\n{owner}\n{expires}".encode()
        return hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()

    def path_for(self, name: str, *, owner_key_id: str | None) -> Path | None:
        """resolve a stored file for its owner (or an admin) without exposing others' media."""
        if not _SAFE_NAME.fullmatch(name):
            return None
        owner = self.owner_of(name)
        if not owner or (owner_key_id != "admin" and owner != owner_key_id):
            return None
        path = self.root / name
        if path.is_symlink() or not path.is_file():
            return None
        return path

    def signed_url(self, name: str, base: str) -> str:
        """issue a short-lived bearer link without exposing the caller's API key."""
        owner = self.owner_of(name)
        if not owner:
            return f"{base}/v1/media/{name}"
        expires = int(time.time()) + self.url_ttl
        query = urlencode({"expires": expires, "signature": self._signature(name, owner, expires)})
        return f"{base}/v1/media/{name}?{query}"

    def valid_signature(self, name: str, expires: int, signature: str) -> bool:
        owner = self.owner_of(name)
        now = int(time.time())
        if not owner or expires < now or expires > now + self.url_ttl or len(signature) != 64:
            return False
        return hmac.compare_digest(self._signature(name, owner, expires), signature)

    def refresh_links(self, value, base: str, owner_key_id: str):
        """refresh only structured URL fields belonging to the authorized task owner."""
        if isinstance(value, list):
            return [self.refresh_links(item, base, owner_key_id) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                parsed = urlsplit(item)
                name = parsed.path.removeprefix("/v1/media/")
                owner = self.owner_of(name) if _SAFE_NAME.fullmatch(name) else None
                if (
                    parsed.path.startswith("/v1/media/")
                    and owner
                    and (owner_key_id == "admin" or owner == owner_key_id)
                ):
                    result[key] = self.signed_url(name, base)
                    continue
            result[key] = (
                self.refresh_links(item, base, owner_key_id)
                if isinstance(item, (dict, list))
                else item
            )
        return result

    def prune(self, retention_days: float, *, now: float | None = None) -> int:
        """Delete expired generated media files; a zero retention disables pruning."""
        if retention_days <= 0:
            return 0

        cutoff = (time.time() if now is None else now) - retention_days * 86400
        deleted = 0
        try:
            entries = self.root.iterdir()
        except OSError:
            log.exception("failed to list media directory %s", self.root)
            return 0

        for path in entries:
            if not _MANAGED_MEDIA_NAME.fullmatch(path.name):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                log.warning("failed to inspect media file %s", path, exc_info=True)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                log.warning("failed to remove expired media file %s", path, exc_info=True)
                continue
            with sqlite3.connect(self._db_path) as db:
                db.execute("DELETE FROM owners WHERE name = ?", (path.name,))
            deleted += 1

        if deleted:
            log.info("pruned %d generated media files older than %s days", deleted, retention_days)
        return deleted

    def path_of(self, name: str) -> Path:
        if not _SAFE_NAME.match(name):
            raise NotFound("media not found")
        p = self.root / name
        if p.is_symlink() or not p.is_file():
            raise NotFound("media not found")
        return p

    @staticmethod
    def mime_of(name: str) -> str:
        extension = Path(name).suffix.lower()
        known = next((mime for mime, suffix in _EXT_BY_MIME.items() if suffix == extension), None)
        return known or mimetypes.guess_type(name)[0] or "application/octet-stream"


async def load_image_ref(
    ref: str, *, timeout: float = 20.0, max_bytes: int = 8 * 1024 * 1024
) -> tuple[bytes, str]:
    """decode bounded raster data or download from a validated, pinned public address."""
    ref = ref.strip()
    if ref.startswith(("http://", "https://")):
        return await download_image(ref, timeout=timeout, max_bytes=max_bytes)
    mime = "image/png"
    payload = ref
    if ref.startswith("data:"):
        header, separator, payload = ref.partition(",")
        mime = header[5:].split(";")[0].lower()
        if not separator or ";base64" not in header:
            raise InvalidRequest("image data URL must use base64")
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}:
        raise InvalidRequest("unsupported image MIME type")
    if len(payload) > ((max_bytes + 2) // 3) * 4:
        raise InvalidRequest("input image exceeds the size limit")
    try:
        data = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise InvalidRequest(
            "image must be a base64 data URL, public http(s) URL or base64 string"
        ) from exc
    if not data or len(data) > max_bytes:
        raise InvalidRequest("input image is empty or exceeds the size limit")
    return data, mime

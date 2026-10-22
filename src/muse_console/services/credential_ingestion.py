"""Bounded candidate validation, source-scoped authorization and CAS account commits."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

from ..accounts.credentials import CredentialEnvelope, project_records
from ..accounts.model import Account, AccountStatus
from .credential_devices import CredentialBoundaryError, digest
from .credential_validator import CredentialValidationError, VerifiedCredential

__all__ = ["CredentialIngestion", "VerifiedCredential"]


def configured_origin(settings) -> str:
    value = getattr(settings, "credential_server_origin", "")
    try:
        p = urlsplit(value)
        loopback = p.hostname in ("127.0.0.1", "localhost", "::1")
        if (
            not p.hostname
            or p.username
            or p.password
            or p.query
            or p.fragment
            or p.path
            or value != f"{p.scheme}://{p.netloc}"
            or (
                p.scheme != "https"
                and not (
                    p.scheme == "http"
                    and loopback
                    and getattr(settings, "credential_allow_loopback_http", False)
                )
            )
        ):
            raise ValueError
        _ = p.port
    except ValueError:
        raise CredentialBoundaryError("invalid_server_origin", 503) from None
    return value


class CredentialIngestion:
    def __init__(self, settings, pool, devices, validator) -> None:
        self.settings, self.pool, self.devices, self.validator = settings, pool, devices, validator
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "credential_ingestion_enabled", False))

    async def startup(self) -> None:
        if self.enabled:
            configured_origin(self.settings)
        await self.devices.startup()
        # Account payload is encrypted by AccountStore. Repair the non-atomic
        # account/receipt boundary before accepting any traffic after restart.
        for account in self.pool.all():
            marker = account.meta.get("credential_ingestion")
            if isinstance(marker, dict):
                self._recover(account, marker)

    async def close(self) -> None:
        async with self._lock:
            await self.devices.close()

    def _subject_match(self, subject: str) -> Account | None:
        """Pool entry already owning this upstream account, if any."""
        for saved in self.pool.all():
            if saved.credential_subject == subject:
                return saved
        return None

    def _recover(self, account: Account, marker: dict) -> None:
        previous = self.devices.binding(marker["source"])
        if not previous or previous["revision"] <= marker["revision"]:
            self.devices.bind(
                source=marker["source"],
                device=marker["device"],
                account=account.id,
                revision=marker["revision"],
                fingerprint=marker["fingerprint"],
                created=marker["created"],
            )
        # Repair just the capture whose encrypted commit succeeded. Older captures
        # already have durable receipts; no raw cookie is ever stored here.
        result = {
            "id": marker["capture"],
            "status": "committed",
            "code": "committed",
            "account_id": account.id,
            "credential_version": marker["version"],
        }
        self.devices.touch_capture(marker["device"], marker["capture"])
        db = self.devices.db
        with db:
            db.execute(
                "INSERT OR REPLACE INTO captures VALUES (?,?,?,?,?,?)",
                (
                    marker["device"],
                    marker["capture"],
                    marker["fingerprint"],
                    marker["source"],
                    marker["revision"],
                    json.dumps(result),
                ),
            )

    @staticmethod
    def source(envelope: CredentialEnvelope) -> str:
        return digest(
            json.dumps(
                [
                    envelope.device_id,
                    envelope.store_id,
                    envelope.partition_key.model_dump() if envelope.partition_key else None,
                ],
                sort_keys=True,
            )
        )

    def confirm(self, device: str, capture: str, account_id: str) -> dict:
        account = self.pool.get(account_id)
        if account is None:
            raise CredentialBoundaryError("account_not_found", 404)
        return self.devices.confirm(device, capture, account_id, version=account.credential_version)

    async def ingest(self, device_or_envelope, envelope: CredentialEnvelope | None = None) -> dict:
        # Accept the authenticated device explicitly, or the already checked envelope.
        if envelope is None:
            envelope = device_or_envelope
        elif device_or_envelope != envelope.device_id:
            raise CredentialBoundaryError("device_mismatch", 403)
        if not self.enabled:
            raise CredentialBoundaryError("ingestion_disabled", 404)
        # Reject overload instead of retaining an unbounded queue of raw secrets.
        if self._lock.locked():
            raise CredentialBoundaryError("ingestion_busy", 429)
        async with self._lock:
            self.devices.active(envelope.device_id)
            self.devices.rate("upload:" + envelope.device_id, limit=30)
            source, fingerprint = self.source(envelope), envelope.fingerprint()
            # Also repair after cancellation/storage errors without needing restart.
            for saved in self.pool.all():
                marker = saved.meta.get("credential_ingestion")
                if isinstance(marker, dict):
                    self._recover(saved, marker)
            old = self.devices.capture(envelope.device_id, envelope.capture_id)
            if old and (
                old["fingerprint"] != fingerprint
                or old["source"] != source
                or old["revision"] != envelope.source_revision
            ):
                raise CredentialBoundaryError("capture_reused", 409)
            if old:
                result = json.loads(old["result"])
                if result["status"] not in (
                    "retry_wait",
                    "awaiting_login",
                    "needs_identity_confirmation",
                    "conflict",
                ):
                    return result
                if result["status"] in (
                    "needs_identity_confirmation",
                    "conflict",
                ) and not self.devices.confirmation(envelope.device_id, envelope.capture_id):
                    return result

            def finish(status: str, code: str, account=None):
                if status == "conflict":
                    self.devices.clear_confirmation(envelope.device_id, envelope.capture_id)
                    account = account or (self.pool.get(target) if target else None)
                result = {"id": envelope.capture_id, "status": status, "code": code}
                if account:
                    result.update(
                        account_id=account.id, credential_version=account.credential_version
                    )
                self.devices.save_receipt(envelope, source, result)
                return result

            binding = self.devices.binding(source)
            confirmed = self.devices.confirmation(envelope.device_id, envelope.capture_id)
            target = (
                confirmed["account"] if confirmed else (binding["account"] if binding else None)
            )
            expected = self.pool.get(target) if target else None
            if target and not expected:
                return finish("conflict", "account_missing")
            if confirmed and expected.credential_version != confirmed["version"]:
                return finish("conflict", "confirmation_version_changed", expected)
            expected_version = expected.credential_version if expected else 0
            if binding and fingerprint == binding["fingerprint"] and not confirmed:
                # This is only a no-op acknowledgment of the CURRENT live snapshot,
                # not a new upstream probe or permission to replay pre-renewal state.
                try:
                    envelope.projection()
                    project_records(expected.credential_records if expected else [])
                except ValueError:
                    return finish("rejected", "expired_or_invalid_snapshot")
                if (
                    not expected
                    or expected.status != AccountStatus.ACTIVE
                    or not expected.enabled
                    or expected.credential_records != envelope.cookies
                ):
                    return finish("conflict", "snapshot_no_longer_current")
                return finish("committed", "unchanged", expected)
            if binding and (
                envelope.source_revision < binding["revision"]
                or (
                    envelope.source_revision == binding["revision"]
                    and (not confirmed or fingerprint != binding["fingerprint"])
                )
            ):
                return finish("conflict", "stale_revision")
            if binding and not confirmed and envelope.base_credential_version != expected_version:
                # The cursor belongs to a bound source; a first-seen source has no
                # meaningful version yet and is matched by subject after probing.
                return finish("conflict", "credential_version_conflict")
            try:
                envelope.projection()
            except ValueError:
                return finish("rejected", "invalid_cookie_snapshot")
            try:
                verified = await asyncio.wait_for(self.validator(envelope), timeout=30)
                if not isinstance(verified, VerifiedCredential):
                    raise ValueError
                cookies = project_records(verified.records)
                if any(
                    r.partitionKey and r.partitionKey != envelope.partition_key
                    for r in verified.records
                ):
                    raise ValueError
            except CredentialValidationError as exc:
                if exc.status in ("awaiting_login", "rejected", "retry_wait"):
                    return finish(exc.status, "validation_failed")
                return finish("retry_wait", "validation_failed")
            except Exception:
                return finish("retry_wait", "validation_failed")
            self.devices.active(envelope.device_id)  # revocation while probe was running
            if expected is None and verified.subject:
                # Upstream reported the account's own workspace id: this capture is
                # the same Muse account as an existing pool entry, so bind the new
                # source to it instead of creating a duplicate account.
                match = self._subject_match(verified.subject)
                if match is not None:
                    expected, target = match, match.id
                    expected_version = match.credential_version
                    binding = None
            if expected:
                # A verified subject wins over an unknown or missing stored one; a
                # conflicting or absent subject still needs a capture-specific
                # admin confirmation (no cookie-based identity guessing).
                if not confirmed and (
                    not verified.subject
                    or (
                        expected.credential_subject
                        and verified.subject != expected.credential_subject
                    )
                ):
                    return finish("needs_identity_confirmation", "identity_confirmation_required")
                if (
                    confirmed
                    and verified.subject
                    and expected.credential_subject
                    and verified.subject != expected.credential_subject
                ):
                    return finish("rejected", "subject_mismatch")
            else:
                if self.devices.remaining(envelope.device_id) <= 0:
                    return finish("needs_identity_confirmation", "new_account_quota_exhausted")
                if verified.subject and any(
                    a.credential_subject == verified.subject for a in self.pool.all()
                ):
                    return finish("needs_identity_confirmation", "identity_confirmation_required")
            account = expected.model_copy(deep=True) if expected else Account()
            version = (expected.credential_version if expected else 0) + 1
            account.cookies = cookies
            account.credential_records = verified.records
            account.cookie_expires = {
                r.name: int(r.expires) for r in verified.records if r.expires is not None
            }
            account.credential_version = version
            account.credential_subject = verified.subject
            account.status = AccountStatus.ACTIVE
            account.clear_error()
            account.cooldown_until = 0
            marker = {
                "source": source,
                "device": envelope.device_id,
                "capture": envelope.capture_id,
                "revision": envelope.source_revision,
                "fingerprint": fingerprint,
                "version": version,
                "created": not expected or bool(binding and binding["created"]),
            }
            account.meta["credential_ingestion"] = marker
            async with self.pool.credential_lock(account.id):
                self.devices.active(envelope.device_id)
                if expected and (
                    self.pool.get(account.id) is not expected
                    or expected.credential_version != expected_version
                ):
                    return finish("conflict", "credential_version_conflict")
                try:
                    if expected:
                        if not await self.pool.upsert_if_current(account, expected):
                            return finish("conflict", "credential_version_conflict")
                    else:
                        account = await self.pool.upsert(account)
                except ValueError:
                    return finish("needs_identity_confirmation", "duplicate_session")
                except Exception:
                    return finish("retry_wait", "account_commit_failed")
                # If process dies here startup repairs this result from encrypted meta.
                self._recover(account, marker)
                return self.devices.receipt(envelope.device_id, envelope.capture_id)

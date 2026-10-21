"""Offline, exclusive-create directory backups. Never stops a service or deletes files.

Both operations require --stopped: the operator must stop ALL writers first.
Destination must not exist (even an empty directory is refused). Partial output is
left in place on failure for manual inspection, never removed or overwritten.
Backups contain secrets: POSIX 0700/0600 or a Windows current-user-only ACL.
Hashes detect corruption, not malicious replacement of the manifest and payload.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath

MANIFEST = "manifest.json"


def checked_path(value: str | Path) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("parent traversal is forbidden")
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("symlink/reparse paths are forbidden")
    return path


def private_directory(path: Path) -> None:
    # Parent must already exist: do not create or chmod unrelated directories.
    path.mkdir(mode=0o700)
    if os.name == "nt":
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
        )
        sid = next(csv.reader(io.StringIO(result.stdout)))[1]
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
            check=True,
            capture_output=True,
        )
    else:
        path.chmod(0o700)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def inventory(root: Path) -> dict:
    checked_path(root)
    if not root.is_dir():
        raise ValueError("source must be a directory")
    entries = {}
    for path in sorted(root.rglob("*")):
        checked_path(path)
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        safe_name(name)
        if name.casefold() in {existing.casefold() for existing in entries}:
            raise ValueError("case-colliding paths are not portable")
        if stat.S_ISDIR(info.st_mode):
            entries[name] = {"kind": "directory"}
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            if path.name.endswith(("-wal", "-shm", "-journal")):
                raise ValueError(
                    "SQLite sidecars present; stop writers and checkpoint offline first"
                )
            entries[name] = {"kind": "file", "size": info.st_size, "sha256": digest(path)}
        else:
            raise ValueError("special files and hardlinks are forbidden")
    return entries


def safe_name(name: str) -> None:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("invalid manifest path")
    parts = PurePosixPath(name).parts
    if PurePosixPath(name).is_absolute() or any(p in ("..", ".", "") for p in parts):
        raise ValueError("invalid manifest path")
    if PurePosixPath(name).as_posix() != name:
        raise ValueError("non-canonical manifest path")
    # Also refuse Windows aliases/device names, including when produced on Linux.
    for part in parts:
        stem = part.split(".")[0].upper()
        if (
            part.endswith((".", " "))
            or stem in {"CON", "PRN", "AUX", "NUL"}
            or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789")
        ):
            raise ValueError("non-portable manifest path")


def paired(entries: dict) -> None:
    names = set(entries)
    if names & {"media-owners.db", "media-signing.key"}:
        if not {"media-owners.db", "media-signing.key"} <= names:
            raise ValueError("media-owners.db and media-signing.key must travel together")
        if any(entries[n]["kind"] != "file" for n in ("media-owners.db", "media-signing.key")):
            raise ValueError("media database/key must be regular files")
    if names & {"accounts.db", "credentials.key", "credential-devices.db"}:
        if not {"accounts.db", "credentials.key"} <= names:
            raise ValueError("accounts.db and credentials.key must be backed up/restored together")
        if any(entries[n]["kind"] != "file" for n in ("accounts.db", "credentials.key")):
            raise ValueError("database/key must be regular files")


def prepare(source: Path, destination: Path) -> None:
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError("source and destination must be disjoint")
    if destination.exists():
        raise ValueError("destination already exists; nothing will be overwritten")
    if not destination.parent.is_dir():
        raise ValueError("destination parent must already exist")


def copy_entries(source: Path, destination: Path, entries: dict) -> None:
    for name, entry in sorted(entries.items(), key=lambda item: (item[0].count("/"), item[0])):
        safe_name(name)
        original = checked_path(source / name)
        target = checked_path(destination / name)
        if entry["kind"] == "directory":
            target.mkdir(mode=0o700)
        else:
            with original.open("rb") as inp, target.open("xb") as out:
                if os.name != "nt":
                    os.fchmod(out.fileno(), 0o600)
                for block in iter(lambda: inp.read(1024 * 1024), b""):
                    out.write(block)
    if inventory(destination) != entries or inventory(source) != entries:
        raise ValueError("source changed or copy verification failed; partial output retained")


def backup(data: str | Path, output: str | Path, *, stopped: bool = False) -> None:
    if not stopped:
        raise ValueError("explicit stopped-service acknowledgement is required")
    source, destination = checked_path(data), checked_path(output)
    prepare(source, destination)
    entries = inventory(source)
    paired(entries)
    private_directory(destination)
    payload = destination / "payload"
    payload.mkdir(mode=0o700)
    copy_entries(source, payload, entries)
    with (destination / MANIFEST).open("x", encoding="utf-8") as handle:
        if os.name != "nt":
            os.fchmod(handle.fileno(), 0o600)
        json.dump({"version": 1, "entries": entries}, handle, indent=2, sort_keys=True)
        handle.write("\n")


def restore(backup_dir: str | Path, output: str | Path, *, stopped: bool = False) -> None:
    if not stopped:
        raise ValueError("explicit stopped-service acknowledgement is required")
    source, destination = checked_path(backup_dir), checked_path(output)
    prepare(source, destination)
    # Check the whole backup, not just manifest-listed files; no extraction occurs.
    inventory(source)
    if {p.name for p in source.iterdir()} != {MANIFEST, "payload"}:
        raise ValueError("unexpected backup layout")
    manifest_path = source / MANIFEST
    if manifest_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("manifest too large")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != {"version", "entries"} or manifest["version"] != 1:
        raise ValueError("unsupported manifest")
    entries = manifest["entries"]
    if not isinstance(entries, dict):
        raise ValueError("invalid entries")
    for name in entries:
        safe_name(name)
    if inventory(source / "payload") != entries:
        raise ValueError("manifest/hash mismatch")
    paired(entries)
    private_directory(destination)
    copy_entries(source / "payload", destination, entries)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command, source_flag in (("backup", "--data"), ("restore", "--backup")):
        child = sub.add_parser(command)
        child.add_argument(source_flag, dest="source", required=True, type=Path)
        child.add_argument("--output", required=True, type=Path)
        child.add_argument("--stopped", required=True, action="store_true")
    args = parser.parse_args()
    try:
        (backup if args.command == "backup" else restore)(
            args.source, args.output, stopped=args.stopped
        )
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, TypeError):
        parser.exit(
            1,
            "Operation refused/failed; no existing files deleted or overwritten. "
            "Any partial output is retained. Inspect paths, permissions and manifest locally.\n",
        )
    print("Verified offline copy complete; source preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

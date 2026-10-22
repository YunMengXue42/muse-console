"""Offline publication guard for tracked files; findings never include matched values.

Use --history to include reachable blobs and commit/tag metadata from local refs. This is a
limited pattern/path check, not a credential-validity test or security audit.
Ignored files, unreachable objects, GitHub settings and external artifacts are
not inspected. Review commit identity hashes separately before publishing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "github-token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private-ai-session": re.compile(rb"https://claude[.]ai/code/session_[A-Za-z0-9_-]+"),
    "gateway-key": re.compile(rb"\bm2a-[A-Za-z0-9_-]{28,}\b"),
    "session-cookie": re.compile(rb"hatch_sess[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_.%-]{64,})"),
}
PRIVATE_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".har", ".pem", ".key")
PRIVATE_DIRS = {"data", "data-preview", "backups", "browser-profile", "node_modules", ".venv"}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def private_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return (
        any(p in PRIVATE_DIRS for p in parts[:-1])
        or (name == ".env" or name.startswith(".env."))
        and name != ".env.example"
        or name.lower().endswith(PRIVATE_SUFFIXES)
        or name.lower() in {"cookies.json", "cookies.txt", "credentials.json"}
    )


def scan_blob(path: str, data: bytes, object_id: str) -> list[dict]:
    findings = []
    if private_path(path):
        findings.append({"rule": "private-path", "path": path, "object": object_id})
    for rule, pattern in RULES.items():
        for match in pattern.finditer(data):
            findings.append(
                {
                    "rule": rule,
                    "path": path,
                    "object": object_id,
                    "line": data.count(b"\n", 0, match.start()) + 1,
                }
            )
    return findings


def objects(history: bool) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if history:
        # Iterate each tree: one blob may have both a safe and a private pathname.
        commits = git("rev-list", "--all").decode().splitlines()
    else:
        commits = ["HEAD"]
    for commit in commits:
        for record in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not record:
                continue
            meta, raw_path = record.split(b"\t", 1)
            _, kind, oid = meta.split()
            if kind == b"blob":
                result.setdefault(oid.decode(), set()).add(raw_path.decode("utf-8", "replace"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="scan all local reachable refs")
    args = parser.parse_args()
    findings = []
    identities = set()
    blob_count = 0
    if args.history:
        entries = objects(True)
        for oid, paths in entries.items():
            data = git("cat-file", "blob", oid)
            blob_count += 1
            for path in sorted(paths):
                findings.extend(scan_blob(path, data, oid))
        for commit in git("rev-list", "--all").decode().splitlines():
            raw = git("cat-file", "commit", commit)
            findings.extend(scan_blob("<commit-metadata>", raw, commit))
            for email in re.findall(rb"^(?:author|committer) [^\n]*<([^>]+)>", raw, re.M):
                if not email.endswith((b"@users.noreply.github.com", b"noreply@github.com")):
                    identities.add(hashlib.sha256(email.lower()).hexdigest()[:16])
        for oid in git("for-each-ref", "--format=%(objectname)", "refs/tags").decode().splitlines():
            if git("cat-file", "-t", oid).strip() == b"tag":
                raw = git("cat-file", "tag", oid)
                findings.extend(scan_blob("<tag-metadata>", raw, oid))
                for email in re.findall(rb"^tagger [^\n]*<([^>]+)>", raw, re.M):
                    if not email.endswith((b"@users.noreply.github.com", b"noreply@github.com")):
                        identities.add(hashlib.sha256(email.lower()).hexdigest()[:16])
    else:
        for raw in git("ls-files", "-z").split(b"\0"):
            if not raw:
                continue
            path = raw.decode("utf-8", "replace")
            full = ROOT / path
            if full.is_symlink():
                findings.append({"rule": "symlink-review", "path": path})
            elif full.is_file():
                data = full.read_bytes()
                blob_count += 1
                findings.extend(scan_blob(path, data, "working-tree"))
    print(
        json.dumps(
            {
                "scope": "all-local-reachable-refs" if args.history else "tracked-working-tree",
                "blobs_checked": blob_count,
                "findings": findings,
                "identity_hashes_to_review": sorted(identities),
                "limitations": "Pattern/path guard only; no ignored files, unreachable objects, remote settings or credential validity checks.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

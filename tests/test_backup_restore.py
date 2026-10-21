"""Synthetic data only; no project data, credentials or running services."""

import json
import os
import stat
import subprocess

import pytest
from tools.backup_restore import backup, restore


@pytest.fixture
def fake_data(tmp_path):
    data = tmp_path / "fake-data"
    data.mkdir()
    for name, content in {
        "accounts.db": b"fake encrypted db",
        "credentials.key": b"fake key",
        "credential-devices.db": b"fake devices",
        "media-owners.db": b"fake media ownership",
        "media-signing.key": b"fake media signing key",
        "tasks.json": b"[]",
    }.items():
        (data / name).write_bytes(content)
    (data / "media").mkdir()
    (data / "media" / "fake.txt").write_text("synthetic", encoding="utf-8")
    (data / "empty").mkdir()
    return data


def test_roundtrip_preserves_source_and_backup(fake_data, tmp_path):
    archive, restored = tmp_path / "archive", tmp_path / "restored"
    before = {
        str(p.relative_to(fake_data)): p.read_bytes() for p in fake_data.rglob("*") if p.is_file()
    }
    backup(fake_data, archive, stopped=True)
    manifest = (archive / "manifest.json").read_bytes()
    restore(archive, restored, stopped=True)
    for name, content in before.items():
        assert (fake_data / name).read_bytes() == content
        assert (restored / name).read_bytes() == content
        assert (archive / "payload" / name).read_bytes() == content
    assert (archive / "manifest.json").read_bytes() == manifest
    assert (restored / "empty").is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(restored.stat().st_mode) == 0o700
        assert stat.S_IMODE((restored / "credentials.key").stat().st_mode) == 0o600
    else:
        acl = subprocess.run(["icacls", str(restored)], capture_output=True, text=True, check=True)
        assert "(I)" not in acl.stdout


def test_refuse_existing_and_acknowledgement(fake_data, tmp_path):
    output = tmp_path / "output"
    with pytest.raises(ValueError):
        backup(fake_data, output)
    output.mkdir()
    (output / "sentinel").write_text("keep")
    with pytest.raises(ValueError):
        backup(fake_data, output, stopped=True)
    assert (output / "sentinel").read_text() == "keep"
    with pytest.raises(ValueError):
        backup(fake_data, fake_data / "nested", stopped=True)
    with pytest.raises(ValueError):
        backup(fake_data, tmp_path / ".." / "elsewhere", stopped=True)


@pytest.mark.parametrize(
    "missing", ["accounts.db", "credentials.key", "media-owners.db", "media-signing.key"]
)
def test_pair_required(fake_data, tmp_path, missing):
    # Synthetic fixture only.
    (fake_data / missing).rename(fake_data / (missing + ".removed"))
    with pytest.raises(ValueError):
        backup(fake_data, tmp_path / "archive", stopped=True)
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("missing", ["media-owners.db", "media-signing.key"])
def test_restore_media_pair_required_even_with_matching_hashes(fake_data, tmp_path, missing):
    from tools.backup_restore import inventory

    archive = tmp_path / "archive"
    backup(fake_data, archive, stopped=True)
    payload = archive / "payload"
    (payload / missing).rename(payload / (missing + ".removed"))
    # A self-consistent manifest must not bypass the db/key pairing invariant.
    manifest = {"version": 1, "entries": inventory(payload)}
    (archive / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "restored"
    with pytest.raises(ValueError, match="must travel together"):
        restore(archive, output, stopped=True)
    assert not output.exists()
    assert (fake_data / missing).is_file()


def test_wal_refused(fake_data, tmp_path):
    (fake_data / "accounts.db-wal").write_bytes(b"pending")
    with pytest.raises(ValueError):
        backup(fake_data, tmp_path / "archive", stopped=True)


@pytest.mark.parametrize("tamper", ["hash", "traversal", "extra", "missing"])
def test_tamper_refused_before_destination(fake_data, tmp_path, tamper):
    archive, output = tmp_path / "archive", tmp_path / "output"
    backup(fake_data, archive, stopped=True)
    if tamper == "hash":
        (archive / "payload" / "accounts.db").write_bytes(b"corrupt")
    elif tamper == "traversal":
        manifest = json.loads((archive / "manifest.json").read_text())
        manifest["entries"]["../escape"] = {"kind": "directory"}
        (archive / "manifest.json").write_text(json.dumps(manifest))
    elif tamper == "missing":
        (archive / "payload" / "accounts.db").rename(archive / "payload" / "lost")
    else:
        (archive / "unexpected").write_text("extra")
    with pytest.raises(ValueError):
        restore(archive, output, stopped=True)
    assert not output.exists()


def test_symlink_or_junction_refused(fake_data, tmp_path):
    link = tmp_path / "link"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(fake_data)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(fake_data, target_is_directory=True)
    with pytest.raises(ValueError):
        backup(link, tmp_path / "archive", stopped=True)
    with pytest.raises(ValueError):
        backup(fake_data, link / "output", stopped=True)


def test_restore_existing_and_no_ack(fake_data, tmp_path):
    archive = tmp_path / "archive"
    backup(fake_data, archive, stopped=True)
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "out")
    with pytest.raises(ValueError):
        restore(archive, fake_data, stopped=True)
    assert (fake_data / "credentials.key").read_bytes() == b"fake key"

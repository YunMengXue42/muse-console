"""Path guards use only fake browser roots and disposable storage."""

from __future__ import annotations

import os
import socket
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from muse_console.drivers.browser import profile_safety as safety


@pytest.fixture(autouse=True)
def known_roots(tmp_path, monkeypatch):
    roots = (tmp_path / "main-browser/User Data",)
    monkeypatch.setattr(safety, "_known_profile_roots", lambda: roots)
    return roots


def test_normal_tmp_storage_is_allowed_without_creation(tmp_path):
    data = tmp_path / "service"
    profile = data / "profile"
    assert safety.validate_owned_paths(data, profile) == (data.resolve(), profile.resolve())
    assert not data.exists()


def test_relative_paths_are_canonicalized(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data, profile = safety.validate_owned_paths(Path("service"), Path("service/cache/../profile"))
    assert data == tmp_path / "service"
    assert profile == data / "profile"


@pytest.mark.parametrize("suffix", ["", "Default", "Profile 1/Cache", "../User Data/Default"])
def test_main_tree_is_rejected_without_inspection(known_roots, monkeypatch, suffix):
    def forbidden(*args, **kwargs):
        pytest.fail("must reject known browser paths before filesystem inspection")

    monkeypatch.setattr(Path, "lstat", forbidden)
    path = known_roots[0] / suffix
    with pytest.raises(ValueError, match="main browser"):
        safety.validate_owned_paths(path, path / "profile")


def test_parent_data_dir_cannot_contain_main_tree(known_roots):
    data = known_roots[0].parent
    with pytest.raises(ValueError, match="main browser"):
        safety.validate_owned_paths(data, data / "service-profile")


def test_root_data_dir_is_rejected(tmp_path):
    root = Path(tmp_path.anchor)
    with pytest.raises(ValueError, match="main browser"):
        safety.validate_owned_paths(root, tmp_path / "service")


def test_main_profile_rejected_with_safe_data_dir(tmp_path, known_roots):
    with pytest.raises(ValueError, match="main browser"):
        safety.validate_owned_paths(tmp_path / "service", known_roots[0] / "Default")


def test_sibling_name_is_not_a_tree_match(known_roots):
    data = known_roots[0].with_name("User Data-service")
    assert safety.validate_owned_paths(data, data / "profile")[0] == data


def test_profile_outside_data_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        safety.validate_owned_paths(tmp_path / "service", tmp_path / "elsewhere/profile")


@pytest.mark.parametrize("where", ["above", "data", "profile", "dotdot"])
@pytest.mark.parametrize("kind", ["symlink", "junction", "reparse"])
def test_redirected_ancestors_are_rejected(tmp_path, monkeypatch, where, kind):
    base = tmp_path / "storage"
    data = base / "data"
    profile = data / "profile"
    redirected = {"above": base, "data": data, "profile": profile, "dotdot": data / "link"}[where]
    if where == "dotdot":
        profile = data / "link/../profile"
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == redirected:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFDIR,
                st_file_attributes=0 if kind == "symlink" else stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="symlink, junction or reparse"):
        safety.validate_owned_paths(data, profile)
    assert not base.exists()


def test_canonical_path_is_checked_against_known_roots(tmp_path, known_roots, monkeypatch):
    data = tmp_path / "service"
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == data:
            return known_roots[0]
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="main browser"):
        safety.validate_owned_paths(data, data / "profile")
    assert not data.exists()


def test_ancestor_inspection_failure_is_closed(tmp_path, monkeypatch):
    original = Path.lstat
    data = tmp_path / "service"

    def lstat(path, *args, **kwargs):
        if path == data:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="safely inspect"):
        safety.validate_owned_paths(data, data / "profile")


def test_known_roots_are_derived_without_reading_profiles(tmp_path, monkeypatch):
    # Undo autouse root override, keeping every home/env location disposable.
    monkeypatch.undo()
    home = tmp_path / "home"
    local = tmp_path / "local"
    config = tmp_path / "config"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    roots = safety._known_profile_roots()
    assert local / "Google/Chrome/User Data" in roots
    assert local / "Microsoft/Edge/User Data" in roots
    assert local / "Chromium/User Data" in roots
    assert config / "google-chrome" in roots
    assert config / "microsoft-edge" in roots
    assert config / "chromium" in roots
    assert home / "Library/Application Support/Google/Chrome" in roots
    assert home / "Library/Application Support/Microsoft Edge" in roots
    assert home / "Library/Application Support/Chromium" in roots
    assert not home.exists()


@pytest.mark.parametrize(
    "variable",
    [
        "CHROME_USER_DATA_DIR",
        "CHROMIUM_USER_DATA_DIR",
        "EDGE_USER_DATA_DIR",
        "MSEDGE_USER_DATA_DIR",
    ],
)
def test_custom_env_roots_rejected_without_inspection(tmp_path, monkeypatch, variable):
    monkeypatch.undo()
    root = tmp_path / "custom-main"
    monkeypatch.setenv(variable, str(root))

    def forbidden(*args, **kwargs):
        pytest.fail("env main profile must be rejected without filesystem inspection")

    monkeypatch.setattr(Path, "lstat", forbidden)
    for path in (root, root / "Default", root.parent):
        with pytest.raises(ValueError, match="main browser"):
            safety.validate_owned_paths(path, path / "service-profile")


def test_chrome_config_home_roots(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("CHROME_CONFIG_HOME", str(tmp_path / "custom-config"))
    root = tmp_path / "custom-config/google-chrome"
    with pytest.raises(ValueError, match="main browser"):
        safety.validate_owned_paths(root, root / "Default")


@pytest.mark.parametrize("nested", [False, True])
def test_unknown_browser_metadata_rejected_without_writes(tmp_path, nested):
    root = tmp_path / "custom-browser"
    root.mkdir()
    (root / "Local State").write_text("fake state", encoding="utf-8")
    (root / "Default").mkdir()
    data = root / "Default" if nested else root
    with pytest.raises(ValueError, match="manually"):
        safety.validate_owned_paths(data, data / "service-profile")
    assert sorted(p.name for p in root.iterdir()) == ["Default", "Local State"]
    assert (root / "Local State").read_text(encoding="utf-8") == "fake state"


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path case semantics")
def test_windows_case_insensitive_known_root(known_roots):
    path = Path(str(known_roots[0]).swapcase()) / "Default"
    with pytest.raises(ValueError, match="main browser"):
        safety.validate_owned_paths(path, path)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        (".muse2api-owned-profile", b"muse2api browser profile v1\n"),
        (safety.PROFILE_MARKER, b"muse-console browser profile v1\n"),
    ],
)
def test_owned_markers_accept_existing_profile_without_rewrite(tmp_path, name, content):
    profile = tmp_path / "service/profile"
    profile.mkdir(parents=True)
    (profile / name).write_bytes(content)
    (profile / "Local State").write_text("fake state", encoding="utf-8")
    safety.validate_owned_paths(profile.parent, profile)
    safety.claim_or_verify_profile(profile)
    assert sorted(p.name for p in profile.iterdir()) == sorted([name, "Local State"])
    assert (profile / name).read_bytes() == content


@pytest.mark.parametrize("name", [".muse2api-owned-profile", safety.PROFILE_MARKER])
@pytest.mark.parametrize("invalid", ["content", "directory", "reparse", "hardlink"])
def test_invalid_marker_still_fails_closed(tmp_path, monkeypatch, name, invalid):
    profile = tmp_path / "profile"
    profile.mkdir()
    marker = profile / name
    if invalid == "directory":
        marker.mkdir()
    else:
        marker.write_bytes(
            b"muse2api browser profile v1\n"
            if name == ".muse2api-owned-profile"
            else b"muse-console browser profile v1\n"
        )
        if invalid == "content":
            marker.write_bytes(b"not an ownership marker")
        elif invalid in ("reparse", "hardlink"):
            original = safety._metadata

            def metadata(path):
                info = original(path)
                if path == marker:
                    return SimpleNamespace(
                        st_mode=info.st_mode,
                        st_size=info.st_size,
                        st_nlink=2 if invalid == "hardlink" else 1,
                        st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
                        if invalid == "reparse"
                        else 0,
                    )
                return info

            monkeypatch.setattr(safety, "_metadata", metadata)
    with pytest.raises(ValueError, match="invalid project profile ownership marker"):
        safety.claim_or_verify_profile(profile)


def test_nonempty_unmarked_profile_cannot_be_claimed(tmp_path):
    (tmp_path / "unknown").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown browser profile"):
        safety.claim_or_verify_profile(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unknown"]


def test_new_profile_uses_new_marker(tmp_path):
    safety.claim_or_verify_profile(tmp_path)
    assert (tmp_path / safety.PROFILE_MARKER).read_bytes() == b"muse-console browser profile v1\n"
    assert not (tmp_path / ".muse2api-owned-profile").exists()


POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX singleton symlinks")

# Above the default Linux pid_max, so no process can own it here.
DEAD_PID = 4194303


def _dead_locks(profile: Path, socket_target: str = "/tmp/muse-console-gone/SingletonSocket"):
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "SingletonLock").symlink_to(f"{socket.gethostname()}-{DEAD_PID}")
    (profile / "SingletonCookie").symlink_to("14004332992058782949")
    (profile / "SingletonSocket").symlink_to(socket_target)


@POSIX
def test_locks_of_a_dead_owner_are_moved_aside(tmp_path):
    _dead_locks(tmp_path)
    moved = safety.clear_proven_stale_locks(tmp_path)
    assert set(moved) == {"SingletonLock", "SingletonCookie", "SingletonSocket"}
    assert not any((tmp_path / name).is_symlink() for name in moved)
    assert len(list((tmp_path / "stale-locks").rglob("SingletonLock"))) == 1


@POSIX
def test_lock_of_a_live_owner_is_refused(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "SingletonLock").symlink_to(f"{socket.gethostname()}-{os.getpid()}")
    with pytest.raises(RuntimeError, match="still running"):
        safety.clear_proven_stale_locks(tmp_path)
    assert (tmp_path / "SingletonLock").is_symlink()


@POSIX
def test_lock_from_another_host_is_refused(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "SingletonLock").symlink_to(f"other-host-{DEAD_PID}")
    with pytest.raises(RuntimeError, match="not this host"):
        safety.clear_proven_stale_locks(tmp_path)
    assert (tmp_path / "SingletonLock").is_symlink()


@POSIX
def test_surviving_socket_keeps_the_lock(tmp_path):
    socket_path = tmp_path / "live.sock"
    socket_path.write_bytes(b"")
    _dead_locks(tmp_path / "profile", str(socket_path))
    with pytest.raises(RuntimeError, match="socket"):
        safety.clear_proven_stale_locks(tmp_path / "profile")
    assert (tmp_path / "profile" / "SingletonLock").is_symlink()


def test_regular_lock_file_is_never_removed(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "SingletonLock").write_bytes(b"untouched")
    with pytest.raises(RuntimeError, match="refusing to remove browser locks"):
        safety.clear_proven_stale_locks(tmp_path)
    assert (tmp_path / "SingletonLock").read_bytes() == b"untouched"

"""Read-only path guards for service-owned browser storage.

Protected roots come from standard locations and environment variables. Unknown
browser trees are detected by metadata only; browser contents are never read.
Only ChromiumProcess.start may claim/verify the project ownership marker.
"""

from __future__ import annotations

import os
import socket
import stat
import sys
import time
from pathlib import Path

PROFILE_MARKER = ".muse-console-owned-profile"
_MARKER_CONTENT = b"muse-console browser profile v1\n"
# On-disk ownership is a compatibility boundary, not just display branding.
_LEGACY_PROFILE_MARKER = ".muse2api-owned-profile"
_LEGACY_MARKER_CONTENT = b"muse2api browser profile v1\n"
_MIGRATION = (
    "Refusing unknown browser profile; use a new empty directory or explicitly "
    "migrate the old project profile manually. No files have been removed."
)

# Chromium's single-instance markers. They are removed by a clean browser exit,
# but a service stop kills the whole cgroup and always leaves them behind.
_SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")
_LOCKED = "Chromium profile is locked; refusing to remove browser locks"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear_proven_stale_locks(profile: Path, *, hostname: str | None = None) -> list[str]:
    """Move singleton locks whose owning browser is provably gone, else raise.

    Only a symlinked SingletonLock naming this host, with a dead pid and a
    socket that no longer exists, counts as stale. Everything else stays put so
    the caller still refuses to launch over a live or ambiguous profile.
    """
    present = [
        name
        for name in _SINGLETON_FILES
        if (profile / name).is_symlink() or (profile / name).exists()
    ]
    if not present:
        return []
    lock = profile / "SingletonLock"
    if sys.platform == "win32" or not lock.is_symlink():
        raise RuntimeError(_LOCKED)
    owner = os.readlink(lock)
    host, _, pid = owner.rpartition("-")
    if host != (hostname or socket.gethostname()) or not pid.isdigit():
        raise RuntimeError(f"{_LOCKED} (lock '{owner}' is not this host)")
    if _pid_alive(int(pid)):
        raise RuntimeError(f"{_LOCKED} (pid {pid} is still running)")
    socket_link = profile / "SingletonSocket"
    target = os.readlink(socket_link) if socket_link.is_symlink() else ""
    if target and os.path.exists(target):
        raise RuntimeError(f"{_LOCKED} (socket {target} still exists)")
    archive = profile / "stale-locks" / time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    archive.mkdir(parents=True, exist_ok=True)
    for name in present:
        try:
            os.rename(profile / name, archive / name)
        except OSError as exc:
            raise RuntimeError(f"cannot move stale browser lock {name}") from exc
    return present


def _known_profile_roots() -> tuple[Path, ...]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return tuple(
        [
            local / vendor / "User Data"
            for vendor in (
                "Google/Chrome",
                "Google/Chrome Beta",
                "Google/Chrome Dev",
                "Google/Chrome SxS",
                "Microsoft/Edge",
                "Microsoft/Edge Beta",
                "Microsoft/Edge Dev",
                "Microsoft/Edge SxS",
                "Chromium",
            )
        ]
        + [
            config / name
            for name in (
                "google-chrome",
                "google-chrome-beta",
                "google-chrome-unstable",
                "chromium",
                "microsoft-edge",
                "microsoft-edge-beta",
                "microsoft-edge-dev",
            )
        ]
        + [
            home / "Library/Application Support" / name
            for name in (
                "Google/Chrome",
                "Google/Chrome Beta",
                "Google/Chrome Dev",
                "Google/Chrome Canary",
                "Microsoft Edge",
                "Microsoft Edge Beta",
                "Microsoft Edge Dev",
                "Microsoft Edge Canary",
                "Chromium",
            )
        ]
        + [
            Path(value)
            for name in (
                "CHROME_USER_DATA_DIR",
                "CHROMIUM_USER_DATA_DIR",
                "EDGE_USER_DATA_DIR",
                "MSEDGE_USER_DATA_DIR",
            )
            if (value := os.environ.get(name))
        ]
        + [
            Path(os.environ["CHROME_CONFIG_HOME"]) / name
            for name in ("google-chrome", "google-chrome-beta", "google-chrome-unstable")
            if os.environ.get("CHROME_CONFIG_HOME")
        ]
        + [
            home / "snap/chromium/common/chromium",
            home / ".var/app/org.chromium.Chromium/config/chromium",
            home / ".var/app/com.google.Chrome/config/google-chrome",
            home / ".var/app/com.microsoft.Edge/config/microsoft-edge",
        ]
    )


def _absolute(path: Path) -> Path:
    # Preserve '..' until after checking ancestors: link/../owned must not hide a link.
    return path if path.is_absolute() else Path.cwd() / path


def _lexical(path: Path) -> Path:
    # Windows Path comparisons are case insensitive; POSIX comparisons are not.
    return Path(os.path.abspath(path))


def _reject_overlap(path: Path, roots: tuple[Path, ...]) -> None:
    if any(path.is_relative_to(root) or root.is_relative_to(path) for root in roots):
        raise ValueError("owned paths must not overlap a main browser profile tree or its parents")


def _checked_path(path: Path, roots: tuple[Path, ...]) -> Path:
    path = _absolute(Path(path))
    # Reject known trees without even stat-ing a real browser profile.
    _reject_overlap(_lexical(path), roots)
    for ancestor in (*reversed(path.parents), path):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("cannot safely inspect owned path ancestors") from exc
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("owned paths must not contain symlink, junction or reparse ancestors")
    try:
        canonical = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("cannot canonicalize owned path") from exc
    _reject_overlap(canonical, roots)
    return canonical


def _metadata(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("cannot safely inspect profile metadata") from exc


def _reject_unknown_browser_tree(path: Path) -> None:
    for directory in (path, *path.parents):
        # A marker is only a candidate here; start() must verify it before launch.
        if any(
            _metadata(directory / name) is not None
            for name in (PROFILE_MARKER, _LEGACY_PROFILE_MARKER)
        ):
            continue
        if _metadata(directory / "Local State") is not None:
            raise ValueError(_MIGRATION)


def claim_or_verify_profile(profile: Path) -> None:
    """Called only by ChromiumProcess.start, after path and lock checks.

    Exclusively create a marker only in an empty directory. A partial/invalid
    marker fails closed, never gets repaired automatically. This prevents
    accidental reuse, not forgery by a filesystem writer or concurrent mutation.
    Existing unmarked project profiles also require explicit manual migration
    or a new empty directory; never stamp them automatically.
    """
    owned = False
    for name, expected in (
        (PROFILE_MARKER, _MARKER_CONTENT),
        (_LEGACY_PROFILE_MARKER, _LEGACY_MARKER_CONTENT),
    ):
        marker = profile / name
        info = _metadata(marker)
        if info is None:
            continue
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or info.st_size != len(expected)
            or info.st_nlink != 1
        ):
            raise ValueError("invalid project profile ownership marker; " + _MIGRATION)
        try:
            with marker.open("rb") as stream:
                content = stream.read(len(expected) + 1)
        except OSError as exc:
            raise ValueError("cannot verify project profile ownership marker") from exc
        if content != expected:
            raise ValueError("invalid project profile ownership marker; " + _MIGRATION)
        owned = True
    if owned:
        return
    marker = profile / PROFILE_MARKER
    try:
        if next(profile.iterdir(), None) is not None:
            raise ValueError(_MIGRATION)
        # O_EXCL: no overwrite or following an existing marker symlink. A crash
        # during this write leaves an invalid marker and subsequent starts fail.
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_MARKER_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ValueError("cannot exclusively claim project profile; " + _MIGRATION) from exc


def validate_owned_paths(data_dir: Path, profile_dir: Path) -> tuple[Path, Path]:
    """Validate both storage paths and return their absolute canonical forms.

    Raises ValueError on unsafe paths. The profile must be inside data_dir (or
    equal to it). Neither path may overlap a known main browser tree in either
    direction, or traverse a symlink/Windows reparse point, even above data_dir.
    Does not create directories, read profile contents, or remove browser locks.
    Settings.ensure_dirs() and BrowserDriver.startup() should call this before
    writes; ChromiumProcess independently guards its profile at launch time.
    This is not a filesystem sandbox against concurrent ancestor replacement.
    """
    roots = tuple(_lexical(_absolute(root)) for root in _known_profile_roots())
    data = _checked_path(data_dir, roots)
    profile = _checked_path(profile_dir, roots)
    if not profile.is_relative_to(data):
        raise ValueError("browser profile must be inside the configured data_dir")
    _reject_unknown_browser_tree(data)
    _reject_unknown_browser_tree(profile)
    return data, profile

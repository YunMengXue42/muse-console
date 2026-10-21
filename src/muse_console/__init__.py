"""muse-console: OpenAI-compatible gateway for muse.ai."""

from importlib.metadata import PackageNotFoundError, version

try:
    # The installed distribution is the single source of truth, so a release
    # bump in pyproject.toml cannot leave health/admin reporting a stale number.
    __version__ = version("muse-console")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "0.0.0+source"

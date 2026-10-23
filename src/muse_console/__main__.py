from __future__ import annotations

import uvicorn

from .app import create_app
from .config import Settings, get_settings
from .log import setup_logging


def dashboard_url(settings: Settings) -> str:
    if settings.public_base:
        return settings.public_base.rstrip("/") + "/dashboard"
    host = settings.host
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    elif ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{settings.port}/dashboard"


def startup_banner(settings: Settings, *, key_was_generated: bool) -> str:
    """Text printed once at start; the generated key is shown only on first start."""
    lines = [
        f"muse-console listening on {settings.host}:{settings.port}  (driver={settings.driver})",
        f"Console: {dashboard_url(settings)}",
    ]
    if key_was_generated:
        lines += [
            "",
            "First start: generated an API key (also the admin key unless MUSE_CONSOLE_ADMIN_KEY is set).",
            f"  {settings.api_key}",
            f"Saved to {settings.key_file}. It is not printed again.",
            "",
        ]
    elif not settings.admin_key and settings.key_file.is_file():
        lines.append(f"API/admin key is stored in {settings.key_file}")
    return "\n".join(lines)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    key_was_generated = not settings.api_key and not _has_stored_key(settings)
    app = create_app(settings)
    print(startup_banner(settings, key_was_generated=key_was_generated), flush=True)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


def _has_stored_key(settings: Settings) -> bool:
    try:
        return bool(settings.key_file.read_text(encoding="utf-8").strip())
    except OSError:
        return False


if __name__ == "__main__":
    main()

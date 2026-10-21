"""Serve the compiled, same-origin administrative console."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["dashboard"])
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


@router.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    """Direct browser visitors to the management console."""
    return RedirectResponse("/dashboard")


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    """Deliver the public shell without any embedded credentials or account data."""
    page = WEB_ROOT / "dashboard.html"
    if not page.is_file():
        return HTMLResponse("Console assets are missing; build frontend first.", status_code=503)
    return HTMLResponse(
        page.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )

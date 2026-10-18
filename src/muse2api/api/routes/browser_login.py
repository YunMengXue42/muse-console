"""Administrator-only manual login controls; no CDP identifiers leave the server."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from ...services.browser_login import BrowserLoginManager, LoginInput
from ...services.container import Services
from ..deps import get_services, require_admin_key

router = APIRouter(
    prefix="/admin/login-sessions", tags=["admin"], dependencies=[Depends(require_admin_key)]
)


class LoginCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="", max_length=100)


def manager(response: Response, svc: Services = Depends(get_services)) -> BrowserLoginManager:
    response.headers["Cache-Control"] = "no-store"
    return svc.browser_login


@router.post("")
async def create(body: LoginCreate, login: BrowserLoginManager = Depends(manager)) -> dict:
    return await login.create(body.label)


@router.get("/{session_id}")
async def get(session_id: str, login: BrowserLoginManager = Depends(manager)) -> dict:
    return await login.get(session_id)


@router.get("/{session_id}/frame")
async def frame(session_id: str, login: BrowserLoginManager = Depends(manager)) -> dict:
    return await login.frame(session_id)


@router.post("/{session_id}/input")
async def input_event(
    session_id: str, body: LoginInput, login: BrowserLoginManager = Depends(manager)
) -> dict:
    return await login.input(session_id, body)


@router.post("/{session_id}/complete")
async def complete(session_id: str, login: BrowserLoginManager = Depends(manager)) -> dict:
    return await login.complete(session_id)


@router.delete("/{session_id}")
async def cancel(session_id: str, login: BrowserLoginManager = Depends(manager)) -> dict:
    return await login.cancel(session_id)

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...services.container import Services
from ..deps import get_services, require_admin_key

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


class PoolUpdate(BaseModel):
    strategy: Literal["lru", "round_robin", "affinity"]


class AccountImport(BaseModel):
    content: str = Field(max_length=2_000_000)
    preview: bool = False


@router.get("/pool")
async def pool_status(svc: Services = Depends(get_services)) -> dict:
    pool = svc.pool
    return {
        "stats": pool.stats(),
        "strategy": pool.strategy_name,
        "max_concurrency": pool.max_concurrency,
        "cooldown": pool.cooldown,
        "acquire_timeout": pool.acquire_timeout,
        "strategy_persisted": hasattr(pool.store, "save_policy"),
    }


@router.patch("/pool")
async def update_pool(body: PoolUpdate, svc: Services = Depends(get_services)) -> dict:
    await svc.pool.set_strategy(body.strategy)
    return await pool_status(svc)


@router.post("/accounts/import")
async def import_accounts(body: AccountImport, svc: Services = Depends(get_services)) -> dict:
    return await svc.account_manager.import_accounts(body.content, preview=body.preview)

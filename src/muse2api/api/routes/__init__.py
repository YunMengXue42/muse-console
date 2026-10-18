from fastapi import APIRouter

from . import (
    account_pool,
    admin,
    browser_login,
    chat,
    dashboard,
    health,
    images,
    media,
    models,
    responses,
    videos,
)


def build_router() -> APIRouter:
    router = APIRouter()
    for module in (
        health,
        models,
        chat,
        responses,
        images,
        videos,
        media,
        account_pool,
        browser_login,
        admin,
        dashboard,
    ):
        router.include_router(module.router)
    return router

"""Service container wired once at startup and stored on ``app.state.services``."""

from __future__ import annotations

from dataclasses import dataclass

from ..accounts.pool import AccountPool
from ..accounts.store import SQLiteAccountStore
from ..auth.keys import KeyStore
from ..config import Settings
from ..core.matting import Matting
from ..core.media import MediaStore
from ..drivers.base import MuseDriver
from ..drivers.registry import create_driver
from .account_manager import AccountManager
from .browser_login import BrowserLoginManager
from .gateway import Gateway
from .request_log import RequestLog, current_record
from .tasks import Task, TaskManager


@dataclass
class Services:
    settings: Settings
    driver: MuseDriver
    pool: AccountPool
    gateway: Gateway
    tasks: TaskManager
    media: MediaStore
    matting: Matting
    keys: KeyStore
    requests: RequestLog
    account_manager: AccountManager
    browser_login: BrowserLoginManager

    @classmethod
    def build(cls, settings: Settings, driver: MuseDriver | None = None) -> Services:
        settings.ensure_dirs()
        driver = driver or create_driver(settings)
        pool = AccountPool(
            SQLiteAccountStore(
                settings.data_dir / "accounts.db",
                key_path=settings.data_dir / "credentials.key",
                legacy_path=settings.accounts_file,
            ),
            strategy=settings.pool_strategy,
            max_concurrency=settings.account_max_concurrency,
            cooldown=settings.account_cooldown,
            acquire_timeout=settings.pool_acquire_timeout,
        )
        account_manager = AccountManager(pool)
        browser_login = BrowserLoginManager(settings, driver, account_manager)
        requests = RequestLog(settings.requests_db)

        async def record_outcome(task: Task) -> None:
            record = current_record.get()
            await requests.finish_task(
                task.id,
                task.status.value,
                task.updated_at,
                (task.error or {}).get("message"),
                record.get("account_id") if record else None,
            )

        return cls(
            settings=settings,
            driver=driver,
            pool=pool,
            gateway=Gateway(pool, driver, max_failover=settings.max_failover),
            tasks=TaskManager(settings.tasks_file, on_finish=record_outcome),
            media=MediaStore(settings.media_dir),
            matting=Matting(settings.matting_model),
            keys=KeyStore(settings.keys_file),
            requests=requests,
            account_manager=account_manager,
            browser_login=browser_login,
        )

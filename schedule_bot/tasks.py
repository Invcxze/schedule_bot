"""Celery task wrappers around the same refresh/notify/digest logic that used
to run as an in-process asyncio loop (see git history: monitor.run_monitor).

Each `*_task` is a thin, Celery-facing shell: it loads settings, opens a Store
and a fresh aiogram Bot, and hands off to a plain async function below. Those
async functions take their dependencies as arguments so they can be unit
tested with fake service/store/sender objects, without a running worker,
broker, or real Telegram/HTTP calls.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from aiogram import Bot

from .celery_app import app
from .config import Settings, load_settings
from .monitor import Sender, drain_outbox
from .source import ScheduleService, Source, SourceError
from .storage import Store

log = logging.getLogger(__name__)

# One ScheduleService per database path, kept for the life of the worker
# process. Reusing it (instead of a fresh one per task run) is what lets
# ScheduleService.refresh's "missing groups / vanished day headers" safety
# check compare against a real previous fetch rather than an empty baseline.
_services: dict[Path, ScheduleService] = {}


def load_task_settings() -> Settings:
    return load_settings(Path(os.environ.get("BOT_CONFIG", "config.toml")))


def get_service(settings: Settings) -> ScheduleService:
    service = _services.get(settings.database_path)
    if service is None:
        service = ScheduleService(Source(settings), settings)
        _services[settings.database_path] = service
    return service


async def refresh_and_notify(service: ScheduleService, store: Store, sender: Sender) -> int:
    """Fetch the source, queue changed-day notifications, and deliver them."""
    try:
        schedules = await service.refresh(force=True)
    except SourceError as exc:
        log.warning("Scheduled refresh failed: %s", exc)
        return 0
    changed = store.collect_changes(schedules)
    await drain_outbox(store, sender)
    return changed


async def send_morning_digest(
    service: ScheduleService, store: Store, sender: Sender, now: datetime, default_time
) -> int:
    """Queue today+tomorrow for every chat with /morning on whose chosen time (or the
    server-wide default) has passed for today, and deliver it."""
    try:
        schedules = await service.refresh()  # cache_ttl-bounded: usually just-refreshed data.
    except SourceError as exc:
        log.warning("Scheduled digest failed to load schedule: %s", exc)
        return 0
    queued = store.collect_digests(schedules, now, default_time)
    await drain_outbox(store, sender)
    return queued


async def drain_pending(store: Store, sender: Sender) -> None:
    """Retry whatever is still queued from a previous failed delivery attempt."""
    await drain_outbox(store, sender)


async def _with_store_and_bot(body) -> None:
    settings = load_task_settings()
    store = Store(settings.database_path)
    bot = Bot(settings.token)
    try:
        await body(settings, store, Sender(bot))
    finally:
        await bot.session.close()
        store.close()


@app.task(name="schedule_bot.refresh_schedule")
def refresh_schedule_task() -> None:
    async def body(settings, store, sender):
        changed = await refresh_and_notify(get_service(settings), store, sender)
        if changed:
            log.info("Queued %d changed day(s)", changed)

    asyncio.run(_with_store_and_bot(body))


@app.task(name="schedule_bot.send_morning_digest")
def send_morning_digest_task() -> None:
    async def body(settings, store, sender):
        now = datetime.now(settings.timezone)
        queued = await send_morning_digest(
            get_service(settings), store, sender, now, settings.morning_digest_time
        )
        if queued:
            log.info("Queued morning digest for %d chat(s)", queued)

    asyncio.run(_with_store_and_bot(body))


@app.task(name="schedule_bot.drain_pending")
def drain_pending_task() -> None:
    async def body(_settings, store, sender):
        await drain_pending(store, sender)

    asyncio.run(_with_store_and_bot(body))

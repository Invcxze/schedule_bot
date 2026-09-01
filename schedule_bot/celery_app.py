"""Celery entry point: `celery -A schedule_bot.celery_app worker -B`.

Unlike `schedule-bot run` (aiogram polling, started via __main__.main which
loads .env itself), Celery's own CLI imports this module directly, so .env
has to be loaded here too.
"""

import os
from pathlib import Path

from celery import Celery
from dotenv import load_dotenv

from .config import load_settings

load_dotenv(Path.cwd() / ".env")

_settings = load_settings(
    Path(os.environ.get("BOT_CONFIG", "config.toml")), need_token=False
)

app = Celery(
    "schedule_bot",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["schedule_bot.tasks"],
)
app.conf.timezone = str(_settings.timezone)
app.conf.beat_schedule = {
    # Fetches the source, diffs it against every subscription's stored baseline,
    # and delivers any changed-day notifications.
    "refresh-schedule": {
        "task": "schedule_bot.refresh_schedule",
        "schedule": _settings.poll_interval,
    },
    # Checked every minute (cheap: a SQL query, no fetch unless something is
    # actually due) so each chat's own /morning at HH:MM fires on the minute
    # instead of everyone sharing one daily crontab tick.
    "send-morning-digest": {
        "task": "schedule_bot.send_morning_digest",
        "schedule": 60.0,
    },
    # A faster-than-refresh retry pass for anything left in the outbox after a
    # transient Telegram/network failure (rate limits, timeouts).
    "drain-pending-notifications": {
        "task": "schedule_bot.drain_pending",
        "schedule": 120.0,
    },
}

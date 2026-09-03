import argparse
import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv

from .bot import create_router
from .config import load_settings
from .formatting import format_all, format_date
from .logs import attach_file_handler, log_file
from .models import normalize_group
from .monitor import Sender
from .source import ScheduleService, Source
from .storage import Store


async def run(settings):
    """Interactive polling only: refresh/notify/digest run as Celery Beat tasks
    (see celery_app.py, tasks.py) against the same database, driven separately by
    `celery -A schedule_bot.celery_app worker -B`.
    """
    store = Store(settings.database_path)
    service = ScheduleService(Source(settings), settings)
    bot = Bot(settings.token)
    sender = Sender(bot)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(settings, service, store, sender))
    try:
        await bot.set_my_commands(
            [
                BotCommand(command=name, description=description)
                for name, description in (
                    ("group", "Выбрать группу"),
                    ("today", "Пары сегодня"),
                    ("tomorrow", "Пары завтра"),
                    ("all", "Всё расписание"),
                    ("groups", "Список групп"),
                    ("morning", "Утренняя рассылка вкл/выкл"),
                    ("subscribe", "Включить уведомления"),
                    ("unsubscribe", "Отключить уведомления"),
                    ("status", "Состояние подписки"),
                    ("help", "Помощь"),
                )
            ]
        )
        # Refuse to silently replace a webhook configured by another deployment.
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError(
                "У бота включён webhook. Используй отдельный токен или отключи webhook вручную."
            )
        await dispatcher.start_polling(
            bot, close_bot_session=False, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await bot.session.close()
        store.close()


async def inspect_source(settings, args):
    from datetime import date
    from io import BytesIO

    from openpyxl import load_workbook

    source = Source(settings)
    if args.list_sheets:
        workbook = load_workbook(BytesIO(await source.read()), read_only=True, data_only=True)
        try:
            for sheet in workbook:
                print(f"{sheet.title!r} ({sheet.sheet_state})")
        finally:
            workbook.close()
        return
    schedules = await ScheduleService(source, settings).refresh(force=True)
    if not args.group:
        print(f"Распознано групп: {len(schedules)}")
        print("\n".join(sorted(schedules)))
        return
    schedule = schedules.get(normalize_group(args.group))
    if schedule is None:
        raise ValueError("Группа не найдена; запусти check без --group для списка.")
    print(
        format_date(schedule, date.fromisoformat(args.date)) if args.date else format_all(schedule)
    )


def main():
    load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description="Telegram schedule bot")
    parser.add_argument("mode", nargs="?", choices=["run", "check"], default="run")
    parser.add_argument(
        "--config", type=Path, default=Path(os.environ.get("BOT_CONFIG", "config.toml"))
    )
    parser.add_argument("--group")
    parser.add_argument("--date", help="YYYY-MM-DD")
    parser.add_argument("--list-sheets", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = load_settings(args.config, need_token=args.mode == "run")
        if args.mode == "run":
            # Console output stays (docker/journal); also spool to a small rotating
            # file so /log can tail it from inside the running bot process.
            attach_file_handler(logging.getLogger(), log_file(settings.database_path, "bot"))
        asyncio.run(inspect_source(settings, args) if args.mode == "check" else run(settings))
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    main()

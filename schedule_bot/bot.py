import logging
import re
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Settings
from .formatting import format_all, format_date, split_text
from .i18n import DEFAULT_LANGUAGE, LANGUAGES, normalize_language, t
from .models import normalize_group
from .monitor import Sender
from .source import ScheduleService, SourceError
from .storage import Store

log = logging.getLogger(__name__)


def chat_language(saved) -> str:
    return normalize_language(saved["language"]) if saved else DEFAULT_LANGUAGE


def has_group(saved) -> bool:
    return bool(saved and saved["group_name"])


def digest_status_line(lang: str, saved) -> str:
    return t(lang, "digest_on") if saved and saved["daily_digest"] else t(lang, "digest_off")


def digest_time_line(settings: Settings, saved) -> str:
    if saved and saved["digest_time"]:
        return saved["digest_time"]
    return settings.morning_digest_time.strftime("%H:%M")


TIME_ARG = re.compile(r"^(?:at|в)?\s*([01]\d|2[0-3]):([0-5]\d)$")


def parse_time_arg(value: str) -> str | None:
    match = TIME_ARG.fullmatch(value.strip().lower())
    return f"{match[1]}:{match[2]}" if match else None


def topic(message: Message) -> int:
    return (message.message_thread_id or 0) if message.is_topic_message else 0


def keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="show:today"),
                InlineKeyboardButton(text="Завтра", callback_data="show:tomorrow"),
                InlineKeyboardButton(text="Всё", callback_data="show:all"),
            ]
        ]
    )


GROUPS_PAGE_SIZE = 20
GROUPS_QUERY_LIMIT = 24  # Keeps callback_data (64 bytes max) well within Telegram's limit.


def groups_page(
    names: list[str], query: str, page: int, lang: str = DEFAULT_LANGUAGE
) -> tuple[str, InlineKeyboardMarkup | None]:
    if not names:
        return t(lang, "groups_not_found"), None
    total_pages = -(-len(names) // GROUPS_PAGE_SIZE)  # ceil division
    page = max(0, min(page, total_pages - 1))
    start = page * GROUPS_PAGE_SIZE
    chunk = names[start : start + GROUPS_PAGE_SIZE]
    title = f"{t(lang, 'groups_title')} ({len(names)}"
    title += f" · «{query}»)" if query else ")"
    text = title + ":\n" + "\n".join(f"▫️ {name}" for name in chunk)
    if total_pages == 1:
        return text, None
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"groups:{page - 1}:{query}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="groups:noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"groups:{page + 1}:{query}"))
    return text, InlineKeyboardMarkup(inline_keyboard=[nav])


def create_router(
    settings: Settings, service: ScheduleService, store: Store, sender: Sender
) -> Router:
    router = Router()

    async def answer(message: Message, text: str, *, buttons=False, html=False):
        # Quick-view buttons are a one-person convenience; in a shared chat they
        # invite everyone to tap them on someone else's reply, so only offer them in DMs.
        show_buttons = buttons and message.chat.type == ChatType.PRIVATE
        parts = split_text(text)
        for index, part in enumerate(parts):
            await sender.send(
                message.chat.id,
                topic(message),
                part,
                reply_markup=keyboard() if show_buttons and index == len(parts) - 1 else None,
                parse_mode="HTML" if html else None,
            )

    def lang_of(message: Message) -> str:
        return chat_language(store.get(message.chat.id, topic(message)))

    async def allowed(message: Message, bot: Bot) -> bool:
        if message.chat.type == ChatType.PRIVATE or not settings.group_admins_only:
            return True
        # Anonymous group admins use sender_chat. Reject linked-channel impersonation.
        if message.sender_chat:
            return message.sender_chat.id == message.chat.id
        if not message.from_user:
            return False
        lang = lang_of(message)
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        except TelegramAPIError:
            await answer(message, t(lang, "cannot_check_permissions"))
            return False
        if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
            return True
        await answer(message, t(lang, "admin_only"))
        return False

    async def load(message: Message):
        lang = lang_of(message)
        try:
            schedules = await service.refresh()
        except SourceError:
            await answer(message, t(lang, "source_unavailable"))
            return None
        if service.last_error:
            stamp = service.loaded_at.astimezone(settings.timezone).strftime("%d.%m %H:%M")
            await answer(message, t(lang, "source_error_cached", stamp=stamp))
        return schedules

    async def find(message: Message, group_arg: str | None):
        schedules = await load(message)
        if schedules is None:
            return None
        lang = lang_of(message)
        saved = store.get(message.chat.id, topic(message))
        name = normalize_group(group_arg) if group_arg else (saved["group_name"] if saved else "")
        if not name:
            await answer(message, t(lang, "select_group_first"))
            return None
        if name not in schedules:
            candidates = [group for group in schedules if name in group][:15]
            hint = (
                t(lang, "similar_groups", list=", ".join(candidates))
                if candidates
                else t(lang, "see_groups_list")
            )
            await answer(message, t(lang, "group_not_found", name=name) + hint)
            return None
        return schedules[name]

    async def show(message: Message, action: str, group_arg: str | None = None):
        schedule = await find(message, group_arg)
        if schedule is None:
            return
        lang = lang_of(message)
        today = datetime.now(settings.timezone).date()
        if action == "all":
            # Plain text for the length check and the .txt fallback; HTML only for chat display.
            text = format_all(schedule, lang=lang)
            if len(text) > 10000:
                await message.answer_document(
                    BufferedInputFile(
                        text.encode("utf-8"), filename=f"{schedule.group}-schedule.txt"
                    ),
                    caption=f"{t(lang, 'full_schedule_title')} {schedule.group}",
                    message_thread_id=topic(message) or None,
                )
                return
            text = format_all(schedule, as_html=True, lang=lang)
        else:
            day = today + timedelta(days=action == "tomorrow")
            text = format_date(schedule, day, as_html=True, lang=lang)
        # Buttons address the saved group, so omit them for a one-off other-group query.
        await answer(message, text, buttons=not group_arg, html=True)

    async def select(message: Message, bot: Bot, name: str | None):
        if not await allowed(message, bot):
            return
        lang = lang_of(message)
        if not name:
            await answer(message, t(lang, "specify_group"))
            return
        schedule = await find(message, name)
        if schedule is None:
            return
        if service.last_error:
            await answer(message, t(lang, "subscription_blocked"))
            return
        store.subscribe(message.chat.id, topic(message), schedule)
        await answer(
            message,
            t(lang, "group_saved", group=schedule.group, interval=settings.poll_interval // 60),
        )
        today = datetime.now(settings.timezone).date()
        await answer(
            message,
            format_date(schedule, today, as_html=True, lang=lang)
            + "\n\n"
            + format_date(schedule, today + timedelta(days=1), as_html=True, lang=lang),
            buttons=True,
            html=True,
        )

    @router.message(Command("start", "help"))
    async def help_handler(message: Message):
        stamp = settings.morning_digest_time.strftime("%H:%M")
        await answer(message, t(lang_of(message), "help", digest_time=stamp))

    @router.message(Command("group"))
    async def group_handler(message: Message, command: CommandObject, bot: Bot):
        await select(message, bot, command.args)

    @router.message(Command("today", "tomorrow", "all", "week"))
    async def schedule_handler(message: Message, command: CommandObject):
        await show(message, "all" if command.command == "week" else command.command, command.args)

    @router.message(Command("groups"))
    async def groups_handler(message: Message, command: CommandObject):
        schedules = await load(message)
        if schedules is None:
            return
        query = normalize_group(command.args or "")[:GROUPS_QUERY_LIMIT]
        names = sorted(name for name in schedules if query in name)
        text, markup = groups_page(names, query, 0, lang_of(message))
        await sender.send(message.chat.id, topic(message), text, reply_markup=markup)

    @router.message(Command("subscribe"))
    async def subscribe_handler(message: Message, bot: Bot):
        saved = store.get(message.chat.id, topic(message))
        await select(message, bot, saved["group_name"] if has_group(saved) else None)

    @router.message(Command("unsubscribe"))
    async def unsubscribe_handler(message: Message, bot: Bot):
        if await allowed(message, bot):
            store.unsubscribe(message.chat.id, topic(message))
            await answer(message, t(lang_of(message), "unsubscribed"))

    @router.message(Command("morning"))
    async def morning_handler(message: Message, command: CommandObject, bot: Bot):
        if not await allowed(message, bot):
            return
        lang = lang_of(message)
        saved = store.get(message.chat.id, topic(message))
        if not has_group(saved):
            await answer(message, t(lang, "morning_need_group"))
            return
        arg = (command.args or "").strip()
        lowered = arg.lower()
        if lowered in {"on", "off", "вкл", "выкл"}:
            turn_on = lowered in {"on", "вкл"}
            store.set_digest(message.chat.id, topic(message), turn_on)
            if turn_on:
                stamp = digest_time_line(settings, saved)
                await answer(message, t(lang, "morning_on", stamp=stamp))
            else:
                await answer(message, t(lang, "morning_off"))
            return
        if arg:
            time_value = parse_time_arg(arg)
            if time_value is None:
                await answer(message, t(lang, "morning_invalid_time"))
                return
            store.set_digest_time(message.chat.id, topic(message), time_value)
            await answer(message, t(lang, "morning_time_set", stamp=time_value))
            return
        await answer(
            message,
            t(
                lang,
                "morning_status",
                status=digest_status_line(lang, saved),
                stamp=digest_time_line(settings, saved),
                tz=settings.timezone,
            ),
        )

    @router.message(Command("language", "lang"))
    async def language_handler(message: Message, command: CommandObject, bot: Bot):
        if not await allowed(message, bot):
            return
        lang = lang_of(message)
        requested = (command.args or "").strip().lower()
        if requested not in LANGUAGES:
            current = t(lang, f"language_name_{lang}")
            await answer(message, t(lang, "language_usage", current=current))
            return
        store.set_language(message.chat.id, topic(message), requested)
        name = t(requested, f"language_name_{requested}")
        await answer(message, t(requested, "language_set", name=name))

    @router.message(Command("status"))
    async def status_handler(message: Message):
        lang = lang_of(message)
        saved = store.get(message.chat.id, topic(message))
        stamp = (
            service.loaded_at.astimezone(settings.timezone).strftime("%d.%m.%Y %H:%M:%S")
            if service.loaded_at
            else t(lang, "status_never_loaded")
        )
        await answer(
            message,
            t(
                lang,
                "status_block",
                group=saved["group_name"] if has_group(saved) else t(lang, "status_not_selected"),
                subscription=(
                    t(lang, "subscription_on")
                    if saved and saved["enabled"]
                    else t(lang, "subscription_off")
                ),
                digest=digest_status_line(lang, saved),
                digest_time=digest_time_line(settings, saved),
                language=t(lang, f"language_name_{lang}"),
                stamp=stamp,
                tz=settings.timezone,
                interval=settings.poll_interval // 60,
                source_status=(
                    t(lang, "status_source_error")
                    if service.last_error
                    else t(lang, "status_source_ok")
                ),
            ),
        )

    @router.callback_query(F.data.startswith("show:"))
    async def callback_handler(callback: CallbackQuery):
        await callback.answer()
        if isinstance(callback.message, Message):
            action = callback.data.split(":", 1)[1]
            if action in {"today", "tomorrow", "all"}:
                await show(callback.message, action)

    @router.callback_query(F.data.startswith("groups:"))
    async def groups_callback(callback: CallbackQuery):
        await callback.answer()
        if callback.data == "groups:noop" or not isinstance(callback.message, Message):
            return
        _, page_text, query = callback.data.split(":", 2)
        schedules = await load(callback.message)
        if schedules is None:
            return
        names = sorted(name for name in schedules if query in name)
        text, markup = groups_page(names, query, int(page_text), lang_of(callback.message))
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramAPIError:
            pass  # Same page re-requested (Telegram rejects a no-op edit); safe to ignore.

    @router.message(F.migrate_to_chat_id)
    async def migrate_to(message: Message):
        store.migrate_chat(message.chat.id, message.migrate_to_chat_id)

    @router.message(F.migrate_from_chat_id)
    async def migrate_from(message: Message):
        store.migrate_chat(message.migrate_from_chat_id, message.chat.id)

    @router.my_chat_member()
    async def membership(event: ChatMemberUpdated):
        if event.new_chat_member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
            store.disable_chat(event.chat.id)

    @router.message(F.chat.type == ChatType.PRIVATE, F.text)
    async def private_text(message: Message, bot: Bot):
        if message.text.startswith("/"):
            await answer(message, t(lang_of(message), "unknown_command"))
        else:
            await select(message, bot, message.text)

    @router.errors()
    async def errors(event: ErrorEvent):
        # Telegram/HTTP exceptions can embed the bot token in a request URL; sqlite3
        # errors never do, so those are safe to log with a full traceback.
        if isinstance(event.exception, sqlite3.Error):
            log.error("Update handling failed", exc_info=event.exception)
        else:
            log.error("Update handling failed (%s)", type(event.exception).__name__)
        if event.update.message and not isinstance(event.exception, TelegramAPIError):
            try:
                await answer(
                    event.update.message, t(lang_of(event.update.message), "generic_error")
                )
            except TelegramAPIError:
                pass
        if isinstance(event.exception, TelegramRetryAfter):
            log.warning("Telegram asked to retry command later")
        return True

    return router

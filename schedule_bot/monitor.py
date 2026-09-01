import asyncio
import json
import logging
import time

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)

from .storage import Store

log = logging.getLogger(__name__)


class Sender:
    def __init__(self, bot: Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._last_global = 0.0
        self._last_chat: dict[int, float] = {}

    async def send(self, chat_id: int, thread_id: int, text: str, *, parse_mode=None, **kwargs):
        async with self._lock:
            delay = max(
                0,
                0.06 - (time.monotonic() - self._last_global),
                (3.1 if chat_id < 0 else 1.05)
                - (time.monotonic() - self._last_chat.get(chat_id, 0)),
            )
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self.bot.send_message(
                    chat_id,
                    text,
                    message_thread_id=thread_id or None,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                    **kwargs,
                )
            finally:
                self._last_global = time.monotonic()
                self._last_chat[chat_id] = self._last_global


async def drain_outbox(store: Store, sender: Sender):
    failed_chats: set[int] = set()
    for item in store.pending():
        chat_id = item["chat_id"]
        if chat_id in failed_chats:
            continue
        parts = json.loads(item["parts"])
        for index in range(item["next_part"], len(parts)):
            if not store.pending_exists(item["id"]):
                break  # Subscription was changed/unsubscribed while awaiting Telegram.
            try:
                # The outbox only ever carries generated schedule/digest text (see
                # storage.collect_changes/collect_digests), always rendered as HTML.
                await sender.send(chat_id, item["thread_id"], parts[index], parse_mode="HTML")
            except TelegramForbiddenError:
                store.disable_chat(chat_id)
                failed_chats.add(chat_id)
                break
            except TelegramMigrateToChat as exc:
                store.migrate_chat(chat_id, exc.migrate_to_chat_id)
                failed_chats.add(chat_id)
                break
            except TelegramRetryAfter as exc:
                log.warning("Telegram rate limit; notification remains queued")
                await asyncio.sleep(exc.retry_after)
                return
            except (TelegramAPIError, OSError, TimeoutError) as exc:
                log.warning("Notification delivery failed (%s), will retry", type(exc).__name__)
                failed_chats.add(chat_id)
                break
            store.acknowledge_part(item["id"], index + 1, len(parts))

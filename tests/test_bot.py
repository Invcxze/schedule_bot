import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus
from aiogram.types import Chat, ChatMemberAdministrator, Message, Update, User
from test_storage_monitor import schedule

from schedule_bot.bot import create_router, groups_page
from schedule_bot.storage import Store


def test_groups_page_pagination():
    names = [f"G{i:02}" for i in range(45)]

    text, markup = groups_page(names, "", 0)
    assert "(45)" in text and "G00" in text and "G19" in text and "G20" not in text
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["1/3", "▶️"]

    text, markup = groups_page(names, "", 2)
    assert "G40" in text and "G44" in text
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["◀️", "3/3"]

    # A page count of exactly one needs no navigation row at all.
    _, single_page_markup = groups_page(names[:5], "", 0)
    assert single_page_markup is None

    text, markup = groups_page([], "ZZZ", 0)
    assert text == "Группы не найдены." and markup is None


def test_commands_private_groups_admin_permissions_and_other_bot_mentions(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "bot.sqlite3")
        s = schedule()

        class Service:
            loaded_at = datetime.now(UTC)
            last_error = None

            async def refresh(self):
                return {s.group: s, "B26-CSE-02": replace(s, group="B26-CSE-02")}

        class Sender:
            messages = []

            async def send(self, chat_id, thread_id, text, **kwargs):
                self.messages.append((chat_id, thread_id, text, kwargs.get("reply_markup")))

        sender = Sender()
        bot = Bot("123456:TEST_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        bot.get_me = AsyncMock(
            return_value=User(id=123456, is_bot=True, first_name="Bot", username="test_bot")
        )
        admin = ChatMemberAdministrator(
            status=ChatMemberStatus.ADMINISTRATOR,
            user=User(id=10, is_bot=False, first_name="Admin"),
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_send_welcome_messages=False,
        )
        bot.get_chat_member = AsyncMock(return_value=admin)
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(settings, Service(), store, sender))

        async def feed(text, chat_id=10, kind="private", thread=None):
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type=kind),
                from_user=User(id=10, is_bot=False, first_name="User"),
                text=text,
                is_topic_message=bool(thread),
                message_thread_id=thread,
            )
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))

        try:
            await feed("b26-cse-01")
            assert store.get(10)["group_name"] == "B26-CSE-01"
            assert any("сохранена" in text for _, _, text, _ in sender.messages)
            # Quick-view buttons are offered in a DM...
            assert sender.messages[-1][3] is not None
            await feed("/group@test_bot B26-CSE-01", -100, "supergroup", 42)
            assert store.get(-100, 42)["enabled"] == 1
            # ...but not in a group, to avoid inviting everyone to tap them.
            assert sender.messages[-1][3] is None
            await feed("/today@test_bot B26-CSE-02", -100, "supergroup", 42)
            assert store.get(-100, 42)["group_name"] == "B26-CSE-01"
            assert "B26-CSE-02" in sender.messages[-1][2]
            count = len(sender.messages)
            await feed("/today@other_bot", -100, "supergroup", 42)
            assert len(sender.messages) == count
            bot.get_chat_member = AsyncMock(
                return_value=type("Member", (), {"status": ChatMemberStatus.MEMBER})()
            )
            await feed("/group@test_bot B26-CSE-02", -100, "supergroup", 42)
            assert store.get(-100, 42)["group_name"] == "B26-CSE-01"
            assert "администратор" in sender.messages[-1][2]
            # /language and /morning are admin-gated the same way as /group in a group chat.
            await feed("/language@test_bot en", -100, "supergroup", 42)
            assert store.get(-100, 42)["language"] == "ru"
            assert "администратор" in sender.messages[-1][2]
            await feed("/morning@test_bot on", -100, "supergroup", 42)
            assert store.get(-100, 42)["daily_digest"] == 0
            await feed("/unsubscribe@test_bot", -100, "supergroup", 42)
            assert store.get(-100, 42)["enabled"] == 1
            bot.get_chat_member = AsyncMock(return_value=admin)
            await feed("/unsubscribe@test_bot", -100, "supergroup", 42)
            assert store.get(-100, 42)["enabled"] == 0
            assert store.get(10)["enabled"] == 1
            before = len(sender.messages)
            await feed("ordinary chat conversation", -100, "supergroup")
            assert len(sender.messages) == before

            await feed("/morning on")
            assert store.get(10)["daily_digest"] == 1
            assert "включена" in sender.messages[-1][2]
            await feed("/morning off")
            assert store.get(10)["daily_digest"] == 0
            assert "выключена" in sender.messages[-1][2]

            # /morning at HH:MM sets this chat's own digest time, independent of on/off.
            await feed("/morning at 07:30")
            assert store.get(10)["digest_time"] == "07:30"
            assert "07:30" in sender.messages[-1][2]
            await feed("/morning nonsense")
            assert "07:30" in sender.messages[-1][2]  # invalid-time message, not silently ignored
            await feed("/morning")
            assert "07:30" in sender.messages[-1][2]  # status reflects the custom time

            # /language switches this chat's display language (admin-only in a group,
            # but chat 10 here is a DM, where the user is always "admin" of themself).
            await feed("/language nonsense")
            assert "Выбери язык" in sender.messages[-1][2]
            assert store.get(10)["language"] == "ru"
            await feed("/language en")
            assert store.get(10)["language"] == "en"
            assert "switched to English" in sender.messages[-1][2]
            # /all lists every day key regardless of today's real weekday, so this
            # doesn't depend on which day the test happens to run on.
            await feed("/all")
            assert "Monday" in sender.messages[-1][2] and "Tuesday" in sender.messages[-1][2]
            await feed("/language ru")
            assert store.get(10)["language"] == "ru"
        finally:
            store.close()
            await bot.session.close()

    asyncio.run(scenario())

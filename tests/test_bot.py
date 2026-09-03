import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, Chat, ChatMemberAdministrator, Message, Update, User
from test_storage_monitor import schedule

from schedule_bot.bot import create_router, groups_page
from schedule_bot.logs import log_file
from schedule_bot.storage import Store


def test_groups_page_pagination():
    names = [f"G{i:02}" for i in range(45)]

    # 45 names, 8 per page (2 columns x 4 rows) -> 6 pages.
    text, markup = groups_page(names, "", 0)
    assert "(45)" in text
    rows = markup.inline_keyboard
    assert len(rows) == 5  # 4 group rows + 1 nav row
    assert [b.text for b in rows[0]] == ["1 · G00", "2 · G01"]
    assert [b.text for b in rows[3]] == ["7 · G06", "8 · G07"]
    assert rows[0][0].callback_data == "pick:G00"  # tapping a group picks it directly
    assert [b.text for b in rows[-1]] == ["1/6", "▶️"]

    text, markup = groups_page(names, "", 5)
    rows = markup.inline_keyboard
    assert len(rows) == 4  # last page: 5 names -> 3 rows (2+2+1) + nav row
    assert [b.text for b in rows[2]] == ["45 · G44"]
    assert [b.text for b in rows[-1]] == ["◀️", "6/6"]

    # A single page still lists every group as a button, just without a nav row.
    _, single_page_markup = groups_page(names[:5], "", 0)
    single_rows = single_page_markup.inline_keyboard
    assert len(single_rows) == 3
    assert [b.text for b in single_rows[2]] == ["5 · G04"]

    text, markup = groups_page([], "ZZZ", 0)
    assert text == "Группы не найдены." and markup is None


def test_commands_private_groups_admin_permissions_and_other_bot_mentions(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "bot.sqlite3")
        s = schedule()

        class Service:
            loaded_at = datetime.now(UTC)
            last_error = None
            stale_groups = {}

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


def test_status_reports_degraded_source_and_stale_group(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "bot.sqlite3")
        s = schedule()
        store.subscribe(10, 0, s)

        class Service:
            loaded_at = datetime.now(UTC)
            last_error = None
            stale_groups = {s.group: "исчезла из источника"}

            async def refresh(self):
                return {s.group: s}

        class Sender:
            messages = []

            async def send(self, chat_id, thread_id, text, **kwargs):
                self.messages.append((chat_id, thread_id, text, kwargs.get("reply_markup")))

        sender = Sender()
        bot = Bot("123456:TEST_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        bot.get_me = AsyncMock(
            return_value=User(id=123456, is_bot=True, first_name="Bot", username="test_bot")
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(settings, Service(), store, sender))
        try:
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=10, type="private"),
                from_user=User(id=10, is_bot=False, first_name="User"),
                text="/status",
            )
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))

            texts = [m[2] for m in sender.messages]
            # Degraded (not a hard error): the roster mostly loaded fine, just this
            # one group didn't — /status surfaces both the aggregate and the detail.
            assert any("не обновились" in text for text in texts)
            assert any("пропала из последней загрузки" in text for text in texts)
        finally:
            store.close()
            await bot.session.close()

    asyncio.run(scenario())


def test_log_command_is_owner_only_and_requires_configuration(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "bot.sqlite3")

        class Service:
            loaded_at = datetime.now(UTC)
            last_error = None
            stale_groups = {}

            async def refresh(self):
                return {}

        class Sender:
            messages = []

            async def send(self, chat_id, thread_id, text, **kwargs):
                self.messages.append((chat_id, thread_id, text, kwargs.get("reply_markup")))

        sender = Sender()
        bot = Bot("123456:TEST_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        bot.get_me = AsyncMock(
            return_value=User(id=123456, is_bot=True, first_name="Bot", username="test_bot")
        )

        async def feed(dispatcher, chat_id, user_id):
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="User"),
                text="/log",
            )
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))

        try:
            # No owner_user_id configured at all: refuse before checking who asked.
            unconfigured = Dispatcher()
            unconfigured.include_router(create_router(settings, Service(), store, sender))
            await feed(unconfigured, 1, 999)
            assert "не настроена" in sender.messages[-1][2]

            owned = replace(settings, owner_user_id=10)
            log_path = log_file(owned.database_path, "bot")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("2026-09-01 10:00:00,000 INFO bot: distinctive-log-line\n")

            dispatcher = Dispatcher()
            dispatcher.include_router(create_router(owned, Service(), store, sender))

            # Message.answer_document builds a SendDocument bound to the bot and
            # awaits it directly (bot(method), not bot.send_document(...)), so the
            # session itself — not a Bot method — is the mockable seam here.
            class FakeSession:
                def __init__(self):
                    self.calls = []

                async def __call__(self, bot, method, timeout=None):  # noqa: ASYNC109 (matches aiogram's BaseSession.__call__ signature)
                    self.calls.append(method)
                    return None

                async def close(self):
                    pass

            bot.session = FakeSession()

            # A non-owner chat admin/user must not see log content at all.
            await feed(dispatcher, 2, 999)
            assert "владельцу" in sender.messages[-1][2]
            assert bot.session.calls == []

            # The configured owner gets the tailed content, as a file.
            await feed(dispatcher, 3, 10)
            assert len(bot.session.calls) == 1
            document = bot.session.calls[0].document
            assert document.filename == "log.txt"
            assert b"distinctive-log-line" in document.data
        finally:
            store.close()
            await bot.session.close()

    asyncio.run(scenario())


def test_group_without_args_shows_picker_instead_of_a_text_hint(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "bot.sqlite3")
        s = schedule()

        class Service:
            loaded_at = datetime.now(UTC)
            last_error = None
            stale_groups = {}

            async def refresh(self):
                return {s.group: s}

        class Sender:
            messages = []

            async def send(self, chat_id, thread_id, text, **kwargs):
                self.messages.append((chat_id, thread_id, text, kwargs.get("reply_markup")))

        sender = Sender()
        bot = Bot("123456:TEST_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        bot.get_me = AsyncMock(
            return_value=User(id=123456, is_bot=True, first_name="Bot", username="test_bot")
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(settings, Service(), store, sender))
        try:
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=1, type="private"),
                from_user=User(id=1, is_bot=False, first_name="User"),
                text="/group",
            )
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))

            _, _, text, markup = sender.messages[-1]
            assert "Доступные группы" in text
            assert markup is not None
            assert markup.inline_keyboard[0][0].callback_data == f"pick:{s.group}"
        finally:
            store.close()
            await bot.session.close()

    asyncio.run(scenario())


def test_pick_callback_subscribes_and_is_admin_gated(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "bot.sqlite3")
        s = schedule()  # group "B26-CSE-01"

        class Service:
            loaded_at = datetime.now(UTC)
            last_error = None
            stale_groups = {}

            async def refresh(self):
                return {s.group: s}

        class Sender:
            messages = []

            async def send(self, chat_id, thread_id, text, **kwargs):
                self.messages.append((chat_id, thread_id, text, kwargs.get("reply_markup")))

        sender = Sender()
        bot = Bot("123456:TEST_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        bot.get_me = AsyncMock(
            return_value=User(id=123456, is_bot=True, first_name="Bot", username="test_bot")
        )

        # CallbackQuery.answer() and Message.answer_document() both build a raw
        # Method bound to the bot and await it directly (bot(method)), not a
        # plain Bot.xxx(...) call — so the session itself is the mockable seam.
        class FakeSession:
            def __init__(self):
                self.calls = []

            async def __call__(self, bot, method, timeout=None):  # noqa: ASYNC109 (matches aiogram's BaseSession.__call__ signature)
                self.calls.append(method)
                return None

            async def close(self):
                pass

        bot.session = FakeSession()

        picker_message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=-100, type="supergroup"),
            text="pick a group",
        )

        def tap(user_id, update_id):
            callback = CallbackQuery(
                id=str(update_id),
                from_user=User(id=user_id, is_bot=False, first_name="U"),
                chat_instance="ci",
                data="pick:B26-CSE-01",
                message=picker_message,
            ).as_(bot)
            return dispatcher.feed_update(bot, Update(update_id=update_id, callback_query=callback))

        try:
            dispatcher = Dispatcher()
            dispatcher.include_router(create_router(settings, Service(), store, sender))

            # A non-admin tap in a group chat is denied — no subscription created.
            bot.get_chat_member = AsyncMock(
                return_value=type("M", (), {"status": ChatMemberStatus.MEMBER})()
            )
            await tap(1, 1)
            assert store.get(-100) is None
            alerts = [c for c in bot.session.calls if type(c).__name__ == "AnswerCallbackQuery"]
            assert len(alerts) == 1 and alerts[0].show_alert

            # An admin's tap subscribes, exactly like /group B26-CSE-01 would.
            bot.get_chat_member = AsyncMock(
                return_value=type("M", (), {"status": ChatMemberStatus.ADMINISTRATOR})()
            )
            await tap(2, 2)
            assert store.get(-100)["group_name"] == "B26-CSE-01"
        finally:
            store.close()
            await bot.session.close()

    asyncio.run(scenario())

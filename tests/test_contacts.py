import asyncio
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User
from test_storage_monitor import schedule

from schedule_bot.bot import create_router
from schedule_bot.contacts import ContactBook, format_contacts, parse_contacts
from schedule_bot.storage import Store

CONTACTS = """
[[contact]]
name = "Teacher"
aliases = ["T. Teacherov"]
telegram = "@teacher_x"
email = "t@example.edu"
notes = "Room <305>"

[[contact]]
name = "Assistant One"
role = "ta"
courses = ["math"]
phone = "+7 900"

[[contact]]
name = "Somebody Else"
telegram = "not a username"
"""


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_contacts_attach_by_teacher_name_alias_or_course(tmp_path):
    book = ContactBook(write(tmp_path / "contacts.toml", CONTACTS))
    found, missing = book.for_schedule(schedule())
    # Teacher matches the "Teacher" cell; the TA is attached through the "Math" subject.
    assert [c.name for c in found] == ["Teacher", "Assistant One"]
    assert missing == []

    assert [c.name for c in book.search("assistant")] == ["Assistant One"]
    assert [c.name for c in book.search("T. TEACHEROV")] == ["Teacher"]
    assert book.search("nobody") == []


def test_unmatched_teachers_are_reported_for_the_maintainer(tmp_path):
    book = ContactBook(write(tmp_path / "contacts.toml", '[[contact]]\nname = "Nobody"\n'))
    found, missing = book.for_schedule(schedule())
    assert found == [] and missing == ["Teacher"]


def test_format_escapes_text_and_links_only_valid_usernames(tmp_path):
    book = ContactBook(write(tmp_path / "contacts.toml", CONTACTS))
    text = format_contacts(book.all(), scope="B26 <x>", missing=["A&B"], lang="en")
    assert "Professors" in text and "Teaching assistants" in text
    assert "B26 &lt;x&gt;" in text and "Room &lt;305&gt;" in text and "A&amp;B" in text
    assert '<a href="https://t.me/teacher_x">@teacher_x</a>' in text
    assert "not a username" in text and "t.me/not" not in text
    assert "t@example.edu" in text and "+7 900" in text


def test_book_is_optional_reloads_on_change_and_survives_a_bad_edit(tmp_path):
    path = tmp_path / "contacts.toml"
    book = ContactBook(path)
    assert book.all() == ()  # No file: feature simply reports "not configured".

    write(path, CONTACTS)
    assert len(book.all()) == 3
    stat = path.stat()

    write(path, "[[contact]]\nname = ")  # Broken TOML mid-edit.
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert len(book.all()) == 3  # Previous contents kept.

    write(path, '[[contact]]\nname = "Only"\n')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2 * 10**9))
    assert [c.name for c in book.all()] == ["Only"]


@pytest.mark.parametrize(
    "text,error",
    [
        ('[[contact]]\nrole = "ta"\n', "name"),
        ('[[contact]]\nname = "X"\nrole = "dean"\n', "role"),
        ('[[contact]]\nname = "X"\ncourses = "math"\n', "courses"),
        ('[[contact]]\nname = "X"\nemail = 5\n', "email"),
    ],
)
def test_invalid_contacts_fail_fast_at_startup(tmp_path, text, error):
    with pytest.raises(ValueError, match=error):
        ContactBook(write(tmp_path / "contacts.toml", text))
    assert parse_contacts({}) == ()


def test_contacts_command(settings, tmp_path):
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
                self.messages.append((text, kwargs.get("parse_mode")))

        sender = Sender()
        bot = Bot("123456:TEST_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        bot.get_me = AsyncMock(
            return_value=User(id=123456, is_bot=True, first_name="Bot", username="test_bot")
        )
        book = ContactBook(write(tmp_path / "contacts.toml", CONTACTS))
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(settings, Service(), store, sender, book))

        async def feed(text):
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=10, type="private"),
                from_user=User(id=10, is_bot=False, first_name="User"),
                text=text,
            )
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))

        try:
            await feed("/contacts")  # No group chosen yet.
            assert "Сначала выбери группу" in sender.messages[-1][0]

            await feed("/contacts B26-CSE-01")
            text, parse_mode = sender.messages[-1]
            assert parse_mode == "HTML" and "Teacher" in text and "Assistant One" in text

            await feed("/contacts assistant")  # Not a group name: searches contacts.
            assert "Assistant One" in sender.messages[-1][0]
            assert "Teacher</b>" not in sender.messages[-1][0]

            await feed("/contacts zzz")
            assert "Ничего не найдено" in sender.messages[-1][0]

            await feed("/contacts B26-CSE-99")
            assert "не найдена" in sender.messages[-1][0]
        finally:
            store.close()
            await bot.session.close()

    asyncio.run(scenario())


def test_contacts_command_without_a_contacts_file(settings, tmp_path):
    async def scenario():
        sent = []

        class Sender:
            async def send(self, chat_id, thread_id, text, **kwargs):
                sent.append(text)

        store = Store(tmp_path / "bot.sqlite3")
        bot = Bot("123456:TEST_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        bot.get_me = AsyncMock(
            return_value=User(id=123456, is_bot=True, first_name="Bot", username="test_bot")
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(settings, None, store, Sender()))
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=10, type="private"),
            from_user=User(id=10, is_bot=False, first_name="User"),
            text="/contacts",
        )
        try:
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))
            assert "не настроены" in sent[-1]
        finally:
            store.close()
            await bot.session.close()

    asyncio.run(scenario())


def test_multi_teacher_cells_are_split_when_reporting_missing_contacts(tmp_path):
    from dataclasses import replace

    from schedule_bot.models import Lesson

    base = schedule()
    lesson = Lesson("09:00", "10:30", "English", "Vasilyeva/Arslanova/Kharrasova", "101")
    multi = replace(base, days={"w:0": (lesson,)})
    book = ContactBook(write(tmp_path / "contacts.toml", '[[contact]]\nname = "Arslanova"\n'))
    found, missing = book.for_schedule(multi)
    assert [c.name for c in found] == ["Arslanova"]
    assert missing == ["Kharrasova", "Vasilyeva"]

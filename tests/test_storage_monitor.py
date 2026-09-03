import asyncio
import json
from dataclasses import replace
from datetime import datetime, time, timedelta

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage

from schedule_bot.models import GroupSchedule, Lesson
from schedule_bot.monitor import drain_outbox
from schedule_bot.storage import Store


def schedule(room="101"):
    return GroupSchedule(
        "B26-CSE-01",
        ("Main",),
        {
            "w:0": (Lesson("09:00", "10:30", "Math", "Teacher", room),),
            "w:1": (),
            "w:6": (),
        },
    )


def test_initial_silent_unchanged_silent_and_only_changed_day(tmp_path):
    store = Store(tmp_path / "db")
    first = schedule()
    store.subscribe(1, 0, first)
    assert not store.pending()
    assert store.collect_changes({first.group: first}) == 0
    second = schedule("102")
    assert store.collect_changes({second.group: second}) == 1
    text = "".join(json.loads(store.pending()[0]["parts"]))
    assert "Понедельник" in text and "102" in text and "Вторник" not in text
    assert store.collect_changes({second.group: second}) == 0
    store.close()


def test_language_preference_is_carried_over_and_used_for_notifications(tmp_path):
    store = Store(tmp_path / "db")
    # Setting a language before any group is picked must not create a phantom
    # subscription: no group selected yet, so nothing should show as "selected".
    store.set_language(1, 0, "en")
    assert store.get(1, 0)["language"] == "en"
    assert not store.get(1, 0)["group_name"]

    first = schedule()
    store.subscribe(1, 0, first)
    assert store.get(1, 0)["language"] == "en"  # carried over into the real subscription

    assert store.collect_changes({first.group: schedule("999")}) == 1
    text = "".join(json.loads(store.pending()[0]["parts"]))
    assert "Monday" in text and "999" in text and "Понедельник" not in text

    store.set_digest(1, 0, True)
    now = datetime(2026, 8, 31, 9, 0)  # Monday, after the 08:00 default
    assert store.collect_digests({first.group: first}, now, time(8, 0)) == 1
    digest_text = "".join(json.loads(store.pending()[-1]["parts"]))
    assert "Monday" in digest_text and "Tuesday" in digest_text

    # Regrouping keeps the chosen language too, same as the digest opt-in.
    store.subscribe(1, 0, replace(first, group="B26-CSE-02"))
    assert store.get(1, 0)["language"] == "en"
    store.close()


def test_baseline_and_outbox_survive_restart(tmp_path):
    path = tmp_path / "db"
    store = Store(path)
    store.subscribe(-100, 7, schedule())
    store.close()
    store = Store(path)
    assert store.collect_changes({schedule().group: schedule("999")}) == 1
    store.close()
    store = Store(path)
    assert len(store.pending()) == 1
    assert store.pending()[0]["thread_id"] == 7
    assert store.collect_changes({schedule().group: schedule("999")}) == 0
    store.close()


def test_all_lessons_removed_and_moved(tmp_path):
    store = Store(tmp_path / "db")
    first = schedule()
    store.subscribe(1, 0, first)
    moved = replace(first, days={"w:0": (), "w:1": first.days["w:0"], "w:6": ()})
    assert store.collect_changes({first.group: moved}) == 2
    texts = ["".join(json.loads(x["parts"])) for x in store.pending()]
    assert "все занятия" in texts[0] and "Вторник" in texts[1]
    store.close()


def test_group_and_topic_isolation_unsubscribe_and_migration(tmp_path):
    store = Store(tmp_path / "db")
    other = replace(schedule(), group="B26-CSE-02")
    for chat, thread, s in [(1, 0, schedule()), (-1, 10, schedule()), (-1, 20, other)]:
        store.subscribe(chat, thread, s)
    assert store.collect_changes({schedule().group: schedule("999"), other.group: other}) == 2
    store.unsubscribe(-1, 10)
    assert len(store.pending()) == 1 and store.pending()[0]["chat_id"] == 1
    store.migrate_chat(-1, -100)
    assert store.get(-100, 20)["group_name"] == other.group
    assert store.get(1)["group_name"] == schedule().group
    store.close()


def test_missing_group_or_day_does_not_cancel_and_same_group_does_not_reset(tmp_path):
    store = Store(tmp_path / "db")
    first = schedule()
    store.subscribe(1, 0, first)
    store.subscribe(1, 0, schedule("999"))
    assert store.collect_changes({}) == 0
    assert store.collect_changes({first.group: replace(first, days={"w:6": ()})}) == 0
    assert store.collect_changes({first.group: schedule("999")}) == 1
    store.close()


def test_delivery_failure_retries_and_forbidden_disables(tmp_path):
    store = Store(tmp_path / "db")
    first = schedule()
    store.subscribe(1, 0, first)
    store.collect_changes({first.group: schedule("999")})

    class FakeSender:
        fail = True
        sent = []

        async def send(self, chat_id, thread_id, text, **kwargs):
            if self.fail:
                raise TelegramNetworkError(
                    method=SendMessage(chat_id=chat_id, text=text), message="offline"
                )
            self.sent.append(text)

    sender = FakeSender()
    asyncio.run(drain_outbox(store, sender))
    assert len(store.pending()) == 1
    sender.fail = False
    asyncio.run(drain_outbox(store, sender))
    assert len(sender.sent) == 1 and not store.pending()
    store.collect_changes({first.group: schedule("555")})

    class Forbidden:
        async def send(self, chat_id, thread_id, text, **kwargs):
            raise TelegramForbiddenError(
                method=SendMessage(chat_id=chat_id, text=text), message="blocked"
            )

    asyncio.run(drain_outbox(store, Forbidden()))
    assert not store.pending() and not store.get(1)["enabled"]
    store.close()


def test_morning_digest_is_opt_in_daily_and_respects_custom_time(tmp_path):
    store = Store(tmp_path / "db")
    first = schedule()
    store.subscribe(1, 0, first)
    default_time = time(8, 0)

    def now_at(hour, minute, offset_days=0):
        # Monday 2026-08-31: both today and tomorrow have a weekly slot.
        return datetime(2026, 8, 31, hour, minute) + timedelta(days=offset_days)

    # Not opted in: no digest queued.
    assert store.collect_digests({first.group: first}, now_at(9, 0), default_time) == 0

    store.set_digest(1, 0, True)
    # Before the server-wide default time: not due yet today.
    assert store.collect_digests({first.group: first}, now_at(7, 0), default_time) == 0
    assert store.collect_digests({first.group: first}, now_at(8, 0), default_time) == 1
    text = "".join(json.loads(store.pending()[0]["parts"]))
    assert "Понедельник" in text and "Вторник" in text

    # Same day: already sent, not queued again even though it's still due.
    assert store.collect_digests({first.group: first}, now_at(9, 0), default_time) == 0

    # A custom per-chat time overrides the server-wide default, for the next day.
    store.set_digest_time(1, 0, "20:00")
    assert store.collect_digests({first.group: first}, now_at(8, 0, 1), default_time) == 0
    assert store.collect_digests({first.group: first}, now_at(20, 0, 1), default_time) == 1

    # Digest preference and its custom time are carried over when the group changes.
    other = replace(first, group="B26-CSE-02")
    store.subscribe(1, 0, other)
    assert store.get(1, 0)["daily_digest"] == 1
    assert store.get(1, 0)["digest_time"] == "20:00"
    store.close()


def test_digest_due_agrees_with_collect_digests_without_fetching_schedules(tmp_path):
    """digest_due is the cheap precheck send_morning_digest runs before ever
    touching the source (see tasks.py) — it must call "due" exactly when
    collect_digests would actually queue something, using only SQLite state."""
    store = Store(tmp_path / "db")
    first = schedule()
    store.subscribe(1, 0, first)
    default_time = time(8, 0)

    def now_at(hour, minute):
        return datetime(2026, 8, 31, hour, minute)  # Monday 2026-08-31

    # Not opted in: never due, no matter the time.
    assert store.digest_due(now_at(9, 0), default_time) is False

    store.set_digest(1, 0, True)
    # Before the default time: not due yet today.
    assert store.digest_due(now_at(7, 0), default_time) is False
    assert store.digest_due(now_at(8, 0), default_time) is True

    # Actually sending it clears the due flag for the rest of the day.
    assert store.collect_digests({first.group: first}, now_at(8, 0), default_time) == 1
    assert store.digest_due(now_at(9, 0), default_time) is False
    store.close()


def test_long_notification_resumes_at_unsent_part(tmp_path):
    store = Store(tmp_path / "db")
    first = schedule()
    store.subscribe(1, 0, first)
    store.collect_changes({first.group: schedule("X" * 8000)})
    item = store.pending()[0]
    parts = json.loads(item["parts"])
    assert len(parts) > 1
    store.acknowledge_part(item["id"], 1, len(parts))

    class Sender:
        sent = []

        async def send(self, chat_id, thread_id, text, **kwargs):
            self.sent.append(text)

    sender = Sender()
    asyncio.run(drain_outbox(store, sender))
    assert sender.sent == parts[1:]
    assert not store.pending()
    store.close()

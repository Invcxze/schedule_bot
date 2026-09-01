import asyncio
from datetime import datetime, time

from test_storage_monitor import schedule

from schedule_bot.source import SourceError
from schedule_bot.storage import Store
from schedule_bot.tasks import drain_pending, refresh_and_notify, send_morning_digest


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, thread_id, text, **kwargs):
        self.sent.append(text)


class FakeService:
    def __init__(self, current):
        self.current = current

    async def refresh(self, *, force=False):
        if self.current is None:
            raise SourceError("offline")
        return {self.current.group: self.current}


def test_refresh_and_notify_only_queues_and_sends_changed_days(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        store.subscribe(1, 0, schedule())
        sender = FakeSender()

        # Unchanged: nothing queued or sent.
        changed = await refresh_and_notify(FakeService(schedule()), store, sender)
        assert changed == 0 and not sender.sent

        # Changed room: queued and delivered in the same call.
        changed = await refresh_and_notify(FakeService(schedule("777")), store, sender)
        assert changed == 1 and len(sender.sent) == 1 and "777" in sender.sent[0]

        # A failed fetch never touches the baseline or sends anything.
        changed = await refresh_and_notify(FakeService(None), store, sender)
        assert changed == 0 and len(sender.sent) == 1
        store.close()

    asyncio.run(scenario())


def test_send_morning_digest_is_opt_in_and_delivers_immediately(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        first = schedule()
        store.subscribe(1, 0, first)
        sender = FakeSender()
        now = datetime(2026, 8, 31, 9, 0)  # Monday, after the 08:00 default
        default_time = time(8, 0)

        # Not opted in: nothing sent.
        queued = await send_morning_digest(FakeService(first), store, sender, now, default_time)
        assert queued == 0 and not sender.sent

        store.set_digest(1, 0, True)
        queued = await send_morning_digest(FakeService(first), store, sender, now, default_time)
        assert queued == 1 and len(sender.sent) == 1
        assert "Понедельник" in sender.sent[0] and "Вторник" in sender.sent[0]

        # Same day again: already sent, no duplicate.
        queued = await send_morning_digest(FakeService(first), store, sender, now, default_time)
        assert queued == 0 and len(sender.sent) == 1
        store.close()

    asyncio.run(scenario())


def test_drain_pending_retries_previously_failed_delivery(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        store.subscribe(1, 0, schedule())
        store.collect_changes({"B26-CSE-01": schedule("555")})
        assert store.pending()

        sender = FakeSender()
        await drain_pending(store, sender)
        assert not store.pending() and len(sender.sent) == 1
        store.close()

    asyncio.run(scenario())

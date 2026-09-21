import asyncio
from datetime import UTC, date, datetime

import pytest
from test_storage_monitor import schedule
from test_tasks import FakeSender, FakeService

from schedule_bot.source import CachedSchedules, SourceError
from schedule_bot.storage import Store
from schedule_bot.tasks import prime_service, refresh_and_notify

LOADED = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


class Fallback:
    """Stands in for the live ScheduleService; counts fetches."""

    def __init__(self, current=None):
        self.current = current
        self.calls = 0
        self.stale_groups = {}
        self.loaded_at = LOADED
        self.last_error = None
        self.schedules = {}

    async def refresh(self, *, force=False):
        self.calls += 1
        if self.current is None:
            raise SourceError("offline")
        return {self.current.group: self.current}


def test_schedule_round_trips_through_json_and_takes_validity_from_the_caller(tmp_path):
    store = Store(tmp_path / "db")
    original = schedule()
    store.save_schedules({original.group: original}, {"X": "исчезла из источника"}, LOADED)
    loaded = store.load_schedules(date(2026, 8, 31), date(2026, 10, 20))
    assert loaded[original.group].days == original.days
    assert loaded[original.group].sheets == original.sheets
    assert loaded[original.group].valid_until == date(2026, 10, 20)
    state = store.cache_state()
    assert state["loaded_at"] == LOADED.isoformat() and state["last_error"] is None
    store.close()


def test_bot_side_reads_the_cache_and_never_fetches(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        fallback = Fallback(schedule("999"))
        service = CachedSchedules(store, settings, fallback)
        first = schedule()
        store.save_schedules({first.group: first}, {}, LOADED)

        for _ in range(3):
            result = await service.refresh()
        assert fallback.calls == 0
        assert result[first.group].days["w:0"][0].room == "101"
        assert service.loaded_at == LOADED and service.last_error is None

        # The worker publishes a newer copy: picked up on the next call.
        second = schedule("102")
        newer = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)
        store.save_schedules(
            {second.group: second}, {second.group: "исчезли заголовки дней"}, newer
        )
        result = await service.refresh()
        assert result[first.group].days["w:0"][0].room == "102"
        assert service.stale_groups == {second.group: "исчезли заголовки дней"}

        # A failed worker refresh keeps the last good copy and surfaces the error.
        store.save_refresh_error("Google вернул HTTP 403.")
        result = await service.refresh()
        assert result[first.group].days["w:0"][0].room == "102"
        assert service.last_error == "Google вернул HTTP 403."
        assert fallback.calls == 0
        store.close()

    asyncio.run(scenario())


def test_empty_cache_falls_back_to_one_live_fetch_and_stores_it(settings, tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        fallback = Fallback(schedule())
        service = CachedSchedules(store, settings, fallback)
        assert (await service.refresh())["B26-CSE-01"]
        assert fallback.calls == 1
        # Now cached: a second reader (e.g. after a bot restart) needs no fetch.
        again = CachedSchedules(store, settings, Fallback(None))
        assert (await again.refresh())["B26-CSE-01"]
        assert again.fallback.calls == 0

        # A worker error recorded before anything was ever cached is not a schedule.
        empty = Store(tmp_path / "db2")
        empty.save_refresh_error("offline")
        broken = CachedSchedules(empty, settings, Fallback(None))
        with pytest.raises(SourceError):
            await broken.refresh()
        store.close()
        empty.close()

    asyncio.run(scenario())


def test_refresh_and_notify_publishes_to_the_cache_and_records_failures(tmp_path):
    async def scenario():
        store = Store(tmp_path / "db")
        sender = FakeSender()
        await refresh_and_notify(FakeService(schedule("555")), store, sender)
        assert store.load_schedules()["B26-CSE-01"].days["w:0"][0].room == "555"

        await refresh_and_notify(FakeService(None), store, sender)
        state = store.cache_state()
        assert state["last_error"] == "offline"
        assert store.load_schedules()["B26-CSE-01"].days["w:0"][0].room == "555"
        store.close()

    asyncio.run(scenario())


def test_worker_restart_is_primed_from_the_cache(settings, tmp_path):
    store = Store(tmp_path / "db")
    store.save_schedules({"B26-CSE-01": schedule()}, {}, LOADED)

    class Service:
        schedules = {}

    service = Service()
    service.settings = settings
    prime_service(service, store)
    assert "B26-CSE-01" in service.schedules
    store.close()

import asyncio
from dataclasses import replace
from datetime import date

import pytest
from test_parser import fixture_cells, xlsx

from schedule_bot.config import ParserConfig, sheet_id_from_url
from schedule_bot.formatting import format_all, format_date, split_text
from schedule_bot.models import GroupSchedule, Lesson
from schedule_bot.source import ScheduleService, SourceError


def test_failed_refresh_keeps_good_cache_and_does_not_fake_deletions(settings):
    class FakeSource:
        data = xlsx(fixture_cells())
        calls = 0

        async def read(self):
            self.calls += 1
            return self.data

    async def scenario():
        source = FakeSource()
        service = ScheduleService(source, replace(settings, parser=ParserConfig(("Main",))))
        good = await service.refresh()
        assert await service.refresh() is good and source.calls == 1
        source.data = b"<html>login</html>"
        with pytest.raises(SourceError):
            await service.refresh(force=True)
        assert service.schedules is good and service.last_error
        assert await service.refresh() is good
        source.data = xlsx(fixture_cells())
        baseline = await service.refresh(force=True)
        assert service.last_error is None

        # A single vanished group (rename, one column dropped) degrades only that
        # group — everyone else still gets refreshed, and it's not an error.
        source.data = xlsx({k: v for k, v in fixture_cells().items() if k != "C2"})
        partial = await service.refresh(force=True)
        assert service.last_error is None
        assert service.stale_groups == {"B26-CSE-02": "исчезла из источника"}
        assert partial["B26-CSE-02"] is baseline["B26-CSE-02"]  # kept, not cancelled
        assert partial["B26-CSE-01"] is not baseline["B26-CSE-01"]  # still refreshed

        # It reappears next time around: the stale flag clears.
        source.data = xlsx(fixture_cells())
        recovered = await service.refresh(force=True)
        assert service.stale_groups == {} and service.last_error is None

        # Most of the previously known roster gone at once (wrong file, mangled
        # export) is still a real failure, not silently swallowed.
        source.data = xlsx({**fixture_cells(), "B2": "B26-CSE-97 (10)", "C2": "B26-CSE-98 (10)"})
        with pytest.raises(SourceError, match="исчезли или сломались"):
            await service.refresh(force=True)
        assert service.schedules is recovered  # unaffected: old good copy kept as-is

    asyncio.run(scenario())


def _per_group_time_column_cells():
    """Two independent day/time blocks, like the real sheet's per-year-block layout,
    so one group's day headers can be broken without touching the others."""
    return {
        # Block 1: own time column A, a single group in B.
        "B2": "B26-CSE-01 (10)",
        "A3": "MONDAY",
        "A4": "09:00-10:30",
        "B4": "Math",
        "B5": "T1",
        "B6": "101",
        "A7": "TUESDAY",
        "A8": "09:00-10:30",
        "B8": "Chem",
        "B9": "T2",
        "B10": "102",
        # Block 2: own time column D, shared by two groups in E and F.
        "E2": "B26-CSE-02 (20)",
        "F2": "B26-CSE-03 (15)",
        "D3": "MONDAY",
        "D4": "09:00-10:30",
        "E4": "Phys",
        "E5": "T3",
        "E6": "103",
        "F4": "Bio",
        "F5": "T4",
        "F6": "104",
        "D7": "TUESDAY",
        "D8": "09:00-10:30",
        "E8": "Geo",
        "E9": "T5",
        "E10": "105",
        "F8": "Art",
        "F9": "T6",
        "F10": "106",
    }


def test_one_groups_lost_day_header_does_not_block_the_others(settings):
    class FakeSource:
        data = xlsx(_per_group_time_column_cells())
        calls = 0

        async def read(self):
            self.calls += 1
            return self.data

    async def scenario():
        source = FakeSource()
        service = ScheduleService(source, replace(settings, parser=ParserConfig(("Main",))))
        baseline = await service.refresh()
        assert set(baseline["B26-CSE-01"].days) >= {"w:0", "w:1"}

        # Drop Tuesday for CSE-01's own block only; CSE-02/CSE-03 have their own
        # time column (D) and are untouched.
        dropped_tuesday = {
            k: v
            for k, v in _per_group_time_column_cells().items()
            if k not in {"A7", "A8", "B8", "B9", "B10"}
        }
        source.data = xlsx(dropped_tuesday)
        result = await service.refresh(force=True)

        assert service.last_error is None  # not a whole-workbook failure
        assert service.stale_groups == {"B26-CSE-01": "исчезли заголовки дней"}
        assert result["B26-CSE-01"] is baseline["B26-CSE-01"]  # kept, Tuesday not "cancelled"
        assert result["B26-CSE-02"] is not baseline["B26-CSE-02"]  # refreshed normally
        assert result["B26-CSE-03"] is not baseline["B26-CSE-03"]

    asyncio.run(scenario())


def test_first_load_failure_and_cache_backoff(settings):
    class Offline:
        calls = 0

        async def read(self):
            self.calls += 1
            raise SourceError("offline")

    async def scenario():
        source = Offline()
        service = ScheduleService(source, settings)
        for _ in range(2):
            with pytest.raises(SourceError):
                await service.refresh()
        assert source.calls == 1

    asyncio.run(scenario())


def test_formatting_and_long_unicode_messages():
    schedule = GroupSchedule(
        "B26-CSE-01",
        ("Main",),
        {
            "w:0": (Lesson("09:00", "10:30", "<b>Math</b>", "A & B", "108"),),
            "w:6": (),
        },
    )
    text = format_date(schedule, date(2026, 8, 31))
    assert "31.08.2026" in text and "<b>Math</b>" in text and "недельной сетке" in text
    assert "нет данных" in format_date(schedule, date(2026, 9, 1))
    assert "Пар нет" in format_date(schedule, date(2026, 9, 6))
    assert "еженедельно" in format_all(schedule)

    # HTML mode (used for chat display, never for the .txt export) escapes
    # spreadsheet-sourced text so a subject/teacher can't inject markup or break
    # Telegram's HTML parser, while still wrapping our own tags around it.
    html_text = format_date(schedule, date(2026, 8, 31), as_html=True)
    assert "&lt;b&gt;Math&lt;/b&gt;" in html_text and "<b>Math</b>" not in html_text
    assert "A &amp; B" in html_text
    assert f"<b>{schedule.group}</b>" in html_text
    assert html_text.count("<b>") == html_text.count("</b>")
    long = "<tag> &\n" + "🧑" * 4500 + "last line"
    chunks = split_text(long)
    assert "".join(chunks) == long
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 3500 for chunk in chunks)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/table",
        "https://evil.com/spreadsheets/d/id",
        "https://docs.google.com/spreadsheets/d/e/2PACX/pubhtml",
    ],
)
def test_source_url_restriction(url):
    with pytest.raises(ValueError):
        sheet_id_from_url(url)


def test_google_url():
    assert (
        sheet_id_from_url("https://docs.google.com/spreadsheets/d/abc_123/edit#gid=0") == "abc_123"
    )

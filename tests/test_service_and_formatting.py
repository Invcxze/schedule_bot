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
        await service.refresh(force=True)
        assert service.last_error is None
        # Entire group or day disappearance is a structural problem, not a cancellation.
        source.data = xlsx({k: v for k, v in fixture_cells().items() if k != "C2"})
        with pytest.raises(SourceError, match="исчезли группы"):
            await service.refresh(force=True)
        source.data = xlsx(
            {
                k: v
                for k, v in fixture_cells().items()
                if k not in {"A10", "A11", "B11", "B12", "B13"}
            }
        )
        with pytest.raises(SourceError, match="заголовки дней"):
            await service.refresh(force=True)

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

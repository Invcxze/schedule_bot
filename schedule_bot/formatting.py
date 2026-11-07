from datetime import date
from html import escape

from .i18n import DEFAULT_LANGUAGE, t
from .models import GroupSchedule, Lesson, day_label


def _text(value: str, as_html: bool) -> str:
    """Escape dynamic (spreadsheet-sourced) text before embedding it in HTML markup.

    Plain mode (as_html=False) is untouched and stays byte-for-byte what a
    plain-text Telegram message or the .txt export has always produced.
    """
    return escape(value) if as_html else value


# (mark, changed fields). mark: None = unchanged, else "new" | "changed" | "cancelled".
Mark = tuple[str, tuple[str, ...]] | None
_LESSON_FIELDS = (
    ("end", "field_time"),
    ("subject", "field_subject"),
    ("teacher", "field_teacher"),
    ("room", "field_room"),
)


def diff_day(before: tuple[Lesson, ...], after: tuple[Lesson, ...]) -> list[tuple[Lesson, Mark]]:
    """Every lesson of the new day in time order, each marked against the old day.

    A new lesson that starts where an old one (no longer present) started counts as
    that lesson edited, and reports which fields differ; a moved lesson therefore
    shows as cancelled at its old time plus new at the new one. Old lessons with no
    counterpart are kept, marked cancelled, so a removal is visible rather than silent.
    """
    before_set, after_set = set(before), set(after)
    entries: list[tuple[Lesson, Mark]] = [
        (lesson, None) for lesson in after if lesson in before_set
    ]
    gone = [lesson for lesson in before if lesson not in after_set]
    for lesson in (lesson for lesson in after if lesson not in before_set):
        same_start = [old for old in gone if old.start == lesson.start]
        match = next((old for old in same_start if old.subject == lesson.subject), None) or (
            same_start[0] if same_start else None
        )
        if match is None:
            entries.append((lesson, ("new", ())))
            continue
        gone.remove(match)
        fields = tuple(
            label for attr, label in _LESSON_FIELDS if getattr(match, attr) != getattr(lesson, attr)
        )
        entries.append((lesson, ("changed", fields)))
    entries.extend((old, ("cancelled", ())) for old in gone)
    return sorted(entries, key=lambda entry: (entry[0].start, entry[0].end))


def _badge(mark: Mark, lang: str) -> str:
    if mark is None:
        return ""
    kind, fields = mark
    text = t(lang, f"lesson_{kind}")
    if fields:
        text += ": " + ", ".join(t(lang, field) for field in fields)
    return text


def format_day(
    group: str,
    key: str,
    lessons: tuple[Lesson, ...],
    *,
    changed=False,
    as_html=False,
    lang=DEFAULT_LANGUAGE,
    previous: tuple[Lesson, ...] | None = None,
) -> str:
    """`previous` (only with changed=True) is the same day before the edit; when
    given, each lesson is marked new / edited (with what changed) / cancelled."""
    icon = "🔔" if changed else "📅"
    title_key = "title_changed" if changed else "title_schedule"
    title = f"{icon} {t(lang, title_key)}"
    group_text = _text(group, as_html)
    header = f"{title} · <b>{group_text}</b>" if as_html else f"{title} · {group}"
    day_text = day_label(key, lang)
    subheader = f"<i>{day_text}</i>" if as_html else day_text
    lines = [header, subheader]
    if not lessons:
        lines.append(t(lang, "no_lessons") if not changed else t(lang, "no_lessons_changed"))
    if changed and previous is not None:
        entries = diff_day(previous, lessons)
    else:
        entries = [(lesson, None) for lesson in lessons]
    for lesson, mark in entries:
        cancelled = mark is not None and mark[0] == "cancelled"
        badge = _badge(mark, lang)
        time_range = f"{lesson.start}–{lesson.end}"
        subject = _text(lesson.subject, as_html)
        if as_html:
            head = f"🕒 <code>{time_range}</code> · <b>{subject}</b>"
            if cancelled:
                head = f"<s>{head}</s>"
            if badge:
                head += f" — {badge}"
            lines.extend(("", head))
        else:
            head = f"{time_range} · {lesson.subject}"
            lines.extend(("", f"{head} — {badge}" if badge else head))
        if cancelled:
            continue  # Its old teacher/room would only clutter a lesson that no longer happens.
        if lesson.teacher:
            teacher = _text(lesson.teacher, as_html)
            label = t(lang, "teacher_label")
            lines.append(f"👤 {teacher}" if as_html else f"{label}: {lesson.teacher}")
        if lesson.room:
            room = _text(lesson.room, as_html)
            label = t(lang, "room_label")
            lines.append(f"📍 {room}" if as_html else f"{label}: {lesson.room}")
    return "\n".join(lines)


def format_date(schedule: GroupSchedule, day: date, *, as_html=False, lang=DEFAULT_LANGUAGE) -> str:
    group_text = _text(schedule.group, as_html)
    group_disp = f"<b>{group_text}</b>" if as_html else schedule.group
    if not schedule.in_period(day):
        return f"📅 {group_disp} · {day:%d.%m.%Y}\n{t(lang, 'out_of_period')}"
    if day.isoformat() not in schedule.days and f"w:{day.weekday()}" not in schedule.days:
        return f"📅 {group_disp} · {day:%d.%m.%Y}\n{t(lang, 'no_data_for_date')}"
    text = format_day(
        schedule.group, day.isoformat(), schedule.on_date(day), as_html=as_html, lang=lang
    )
    if day.isoformat() not in schedule.days:
        text += "\n\n" + t(lang, "weekly_disclaimer")
    return text


def format_all(schedule: GroupSchedule, *, as_html=False, lang=DEFAULT_LANGUAGE) -> str:
    group_text = _text(schedule.group, as_html)
    sheets_text = _text(", ".join(schedule.sheets), as_html)
    group_disp = f"<b>{group_text}</b>" if as_html else schedule.group
    lines = [
        f"{t(lang, 'full_schedule_title')} · {group_disp}",
        f"{t(lang, 'sheet_label')}: {sheets_text}",
    ]
    if schedule.valid_from or schedule.valid_until:
        not_specified = t(lang, "not_specified")
        lines.append(
            f"{t(lang, 'period_label')}: {schedule.valid_from or not_specified}"
            f" — {schedule.valid_until or not_specified}"
        )
    lines.append(t(lang, "weekly_grid_note"))
    keys = sorted(schedule.days, key=lambda key: (not key.startswith("w:"), key))
    for key in keys:
        lines.extend(
            ("", format_day(schedule.group, key, schedule.days[key], as_html=as_html, lang=lang))
        )
    return "\n".join(lines)


def split_text(text: str, limit: int = 3500) -> list[str]:
    """Plain text, measured in UTF-16 units; no split HTML tags or entities."""
    chunks = []
    while text:
        units, end = 0, 0
        for char in text:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > limit:
                break
            units += size
            end += 1
        if end < len(text):
            newline = text.rfind("\n", 0, end)
            if newline > end // 2:
                end = newline + 1
        if end == 0:
            raise ValueError("Message limit too small")
        chunks.append(text[:end])
        text = text[end:]
    return chunks

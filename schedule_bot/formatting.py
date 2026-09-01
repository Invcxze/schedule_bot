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


def format_day(
    group: str,
    key: str,
    lessons: tuple[Lesson, ...],
    *,
    changed=False,
    as_html=False,
    lang=DEFAULT_LANGUAGE,
) -> str:
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
    for lesson in lessons:
        time_range = f"{lesson.start}–{lesson.end}"
        subject = _text(lesson.subject, as_html)
        if as_html:
            lines.extend(("", f"🕒 <code>{time_range}</code> · <b>{subject}</b>"))
        else:
            lines.extend(("", f"{time_range} · {lesson.subject}"))
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

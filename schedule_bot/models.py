import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date

from .i18n import t


def clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def normalize_group(value: str) -> str:
    value = clean(value).upper()
    value = re.sub(r"\s*\(\d+\)\s*$", "", value)
    value = re.sub(r"[‐‑‒–—−]", "-", value)
    return re.sub(r"\s+", "", value)


@dataclass(frozen=True, order=True)
class Lesson:
    start: str
    end: str
    subject: str
    teacher: str = ""
    room: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class GroupSchedule:
    group: str
    sheets: tuple[str, ...]
    days: dict[str, tuple[Lesson, ...]]
    valid_from: date | None = None
    valid_until: date | None = None

    def on_date(self, day: date) -> tuple[Lesson, ...]:
        if not self.in_period(day):
            return ()
        return self.days.get(day.isoformat(), self.days.get(f"w:{day.weekday()}", ()))

    def in_period(self, day: date) -> bool:
        return not (
            (self.valid_from and day < self.valid_from)
            or (self.valid_until and day > self.valid_until)
        )

    def snapshot(self) -> dict[str, list[dict[str, str]]]:
        return {
            day: [lesson.to_dict() for lesson in lessons]
            for day, lessons in sorted(self.days.items())
        }


def day_label(key: str, lang: str = "ru") -> str:
    weekdays = t(lang, "weekdays")
    if key.startswith("w:"):
        return weekdays[int(key[2:])] + t(lang, "weekly_suffix")
    day = date.fromisoformat(key)
    return f"{weekdays[day.weekday()]}, {day:%d.%m.%Y}"

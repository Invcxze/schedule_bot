"""Contacts of professors and TAs, kept in a hand-edited TOML file.

The schedule sheet only carries teacher names, so contact details (Telegram,
email, phone) live in `contacts.toml` next to `config.toml`. The file is re-read
when its modification time changes, so editing it needs no bot restart; a file
that becomes invalid after startup is ignored (previous contents stay in use).
"""

import logging
import re
import tomllib
from dataclasses import dataclass
from html import escape
from pathlib import Path

from .i18n import DEFAULT_LANGUAGE, t
from .models import GroupSchedule, clean

log = logging.getLogger(__name__)

ROLES = ("professor", "ta")
TELEGRAM_USERNAME = re.compile(r"@?([A-Za-z][A-Za-z0-9_]{4,31})")


def _norm(value: str) -> str:
    """Case-, spacing- and punctuation-insensitive form ("Иванов И.И." ~ "иванов и и")."""
    return " ".join(re.sub(r"[.,;]", " ", clean(value).casefold()).split())


@dataclass(frozen=True)
class Contact:
    name: str
    role: str = "professor"
    telegram: str = ""
    email: str = ""
    phone: str = ""
    # Extra spellings of the name as it appears in the schedule sheet.
    aliases: tuple[str, ...] = ()
    # Subjects taught/assisted (substring of the lesson subject); this is how a TA,
    # who usually isn't named in the sheet, gets attached to a group.
    courses: tuple[str, ...] = ()
    notes: str = ""

    def teaches(self, schedule: GroupSchedule) -> bool:
        names = [n for n in map(_norm, (self.name, *self.aliases)) if n]
        courses = [c for c in map(_norm, self.courses) if c]
        for lessons in schedule.days.values():
            for lesson in lessons:
                teacher, subject = _norm(lesson.teacher), _norm(lesson.subject)
                if any(n in teacher for n in names) or any(c in subject for c in courses):
                    return True
        return False

    def matches_query(self, query: str) -> bool:
        needle = _norm(query)
        haystack = (self.name, *self.aliases, *self.courses)
        return bool(needle) and any(needle in _norm(value) for value in haystack)


def _strings(entry: dict, key: str, where: str) -> tuple[str, ...]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}: {key} должен быть списком строк.")
    return tuple(item.strip() for item in value if item.strip())


def parse_contacts(raw: dict) -> tuple[Contact, ...]:
    entries = raw.get("contact", [])
    if not isinstance(entries, list):
        raise ValueError("Ожидались блоки [[contact]].")
    contacts = []
    for index, entry in enumerate(entries, 1):
        where = f"[[contact]] №{index}"
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}: не указано name.")
        role = str(entry.get("role", "professor")).strip().lower()
        if role not in ROLES:
            raise ValueError(f"{where} ({name}): role должен быть одним из {', '.join(ROLES)}.")
        fields = {}
        for key in ("telegram", "email", "phone", "notes"):
            value = entry.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{where} ({name}): {key} должен быть строкой.")
            fields[key] = value.strip()
        contacts.append(
            Contact(
                name.strip(),
                role,
                aliases=_strings(entry, "aliases", where),
                courses=_strings(entry, "courses", where),
                **fields,
            )
        )
    return tuple(contacts)


class ContactBook:
    def __init__(self, path: Path | None):
        self.path = path
        self._mtime: int | None = None
        self._contacts: tuple[Contact, ...] = ()
        self._reload(strict=True)

    def _reload(self, *, strict: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime_ns if self.path else None
        except FileNotFoundError:
            mtime = None
        if mtime == self._mtime and (mtime is not None or self.path is None):
            return
        if mtime is None:
            self._mtime, self._contacts = None, ()
            return
        try:
            with self.path.open("rb") as file:
                contacts = parse_contacts(tomllib.load(file))
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            message = f"{self.path.name}: {exc}"
            if strict:
                raise ValueError(message) from exc
            log.warning("Contacts file ignored, keeping the previous contents (%s)", message)
            self._mtime = mtime  # Don't re-log on every command until the file changes again.
            return
        self._mtime, self._contacts = mtime, contacts

    def all(self) -> tuple[Contact, ...]:
        self._reload()
        return self._contacts

    def for_schedule(self, schedule: GroupSchedule) -> tuple[list[Contact], list[str]]:
        """Contacts attached to this group, plus teacher strings from the schedule
        that match nobody — a to-do list for whoever maintains contacts.toml."""
        contacts = self.all()
        found = [contact for contact in contacts if contact.teaches(schedule)]
        matched_names = [
            n for contact in found for n in map(_norm, (contact.name, *contact.aliases)) if n
        ]
        # A cell can hold several teachers ("Vasilyeva/Arslanova/..."); list them one by one.
        missing = sorted(
            {
                part.strip()
                for lessons in schedule.days.values()
                for lesson in lessons
                for part in lesson.teacher.split("/")
                if part.strip() and not any(n in _norm(part) for n in matched_names)
            }
        )
        return found, missing

    def search(self, query: str) -> list[Contact]:
        return [contact for contact in self.all() if contact.matches_query(query)]


def _telegram_link(value: str) -> str:
    match = TELEGRAM_USERNAME.fullmatch(value)
    if not match:
        return escape(value)
    return f'<a href="https://t.me/{match[1]}">@{match[1]}</a>'


def format_contacts(
    contacts: list[Contact], *, scope: str, missing: list[str] = (), lang: str = DEFAULT_LANGUAGE
) -> str:
    lines = [f"📇 {t(lang, 'contacts_title')} · <b>{escape(scope)}</b>"]
    for role, title_key in (("professor", "contacts_professors"), ("ta", "contacts_tas")):
        group = [contact for contact in contacts if contact.role == role]
        if not group:
            continue
        lines.extend(("", f"<b>{t(lang, title_key)}</b>"))
        for contact in group:
            lines.extend(("", f"👤 <b>{escape(contact.name)}</b>"))
            details = [
                part
                for part in (
                    f"✈️ {_telegram_link(contact.telegram)}" if contact.telegram else "",
                    f"✉️ {escape(contact.email)}" if contact.email else "",
                    f"📞 {escape(contact.phone)}" if contact.phone else "",
                )
                if part
            ]
            if details:
                lines.append(" · ".join(details))
            if contact.notes:
                lines.append(f"<i>{escape(contact.notes)}</i>")
    if missing:
        lines.extend(("", t(lang, "contacts_missing", names=escape(", ".join(missing)))))
    return "\n".join(lines)

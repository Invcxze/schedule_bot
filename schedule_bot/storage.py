import json
import logging
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path

from .formatting import format_date, format_day, split_text
from .i18n import DEFAULT_LANGUAGE, normalize_language, t
from .models import GroupSchedule


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    hours, _, minutes = value.partition(":")
    return time(int(hours), int(minutes))


class Store:
    """Single event-loop connection. Snapshots + notification outbox are atomic."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL DEFAULT 0,
                group_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                snapshot TEXT NOT NULL,
                UNIQUE(chat_id, thread_id)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
                parts TEXT NOT NULL,
                next_part INTEGER NOT NULL DEFAULT 0
            );
        """)
        # Added after the initial release: migrate existing databases in place.
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(subscriptions)")}
        if "daily_digest" not in columns:
            self.db.execute(
                "ALTER TABLE subscriptions ADD COLUMN daily_digest INTEGER NOT NULL DEFAULT 0"
            )
        if "digest_date" not in columns:
            self.db.execute("ALTER TABLE subscriptions ADD COLUMN digest_date TEXT")
        if "language" not in columns:
            # DEFAULT_LANGUAGE is a hardcoded constant, not user input; ALTER TABLE ADD
            # COLUMN doesn't accept a bound parameter in its DEFAULT clause.
            self.db.execute(
                "ALTER TABLE subscriptions ADD COLUMN language TEXT NOT NULL "
                f"DEFAULT '{DEFAULT_LANGUAGE}'"
            )
        if "digest_time" not in columns:
            # NULL means "use the server-wide default" (settings.morning_digest_time).
            self.db.execute("ALTER TABLE subscriptions ADD COLUMN digest_time TEXT")
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, chat_id: int, thread_id: int = 0):
        return self.db.execute(
            "SELECT * FROM subscriptions WHERE chat_id=? AND thread_id=?", (chat_id, thread_id)
        ).fetchone()

    def subscribe(self, chat_id: int, thread_id: int, schedule: GroupSchedule):
        old = self.get(chat_id, thread_id)
        if old and old["enabled"] and old["group_name"] == schedule.group:
            return  # Do not reset a live baseline and accidentally swallow changes.
        # The morning-digest opt-in, its custom time, and the display language are
        # per-chat preferences, not tied to one group's baseline: carry them over
        # across a group change or an unsubscribe/subscribe cycle.
        daily_digest = old["daily_digest"] if old else 0
        digest_time = old["digest_time"] if old else None
        language = old["language"] if old else DEFAULT_LANGUAGE
        with self.db:
            self.db.execute(
                "DELETE FROM subscriptions WHERE chat_id=? AND thread_id=?", (chat_id, thread_id)
            )
            self.db.execute(
                "INSERT INTO subscriptions(chat_id, thread_id, group_name, snapshot, "
                "daily_digest, digest_time, language) VALUES (?,?,?,?,?,?,?)",
                (
                    chat_id,
                    thread_id,
                    schedule.group,
                    json.dumps(schedule.snapshot(), ensure_ascii=False),
                    daily_digest,
                    digest_time,
                    language,
                ),
            )

    def set_digest(self, chat_id: int, thread_id: int, enabled: bool):
        with self.db:
            self.db.execute(
                "UPDATE subscriptions SET daily_digest=? WHERE chat_id=? AND thread_id=?",
                (int(enabled), chat_id, thread_id),
            )

    def set_digest_time(self, chat_id: int, thread_id: int, value: str | None):
        """value is 'HH:MM', or None to fall back to the server-wide default."""
        with self.db:
            self.db.execute(
                "UPDATE subscriptions SET digest_time=? WHERE chat_id=? AND thread_id=?",
                (value, chat_id, thread_id),
            )

    def set_language(self, chat_id: int, thread_id: int, language: str):
        with self.db:
            self.db.execute(
                "INSERT INTO subscriptions(chat_id, thread_id, group_name, snapshot, language) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(chat_id, thread_id) DO UPDATE SET language=excluded.language",
                (chat_id, thread_id, "", "{}", normalize_language(language)),
            )

    def unsubscribe(self, chat_id: int, thread_id: int):
        with self.db:
            row = self.get(chat_id, thread_id)
            if row:
                self.db.execute("UPDATE subscriptions SET enabled=0 WHERE id=?", (row["id"],))
                self.db.execute("DELETE FROM outbox WHERE subscription_id=?", (row["id"],))

    def disable_chat(self, chat_id: int):
        with self.db:
            self.db.execute("UPDATE subscriptions SET enabled=0 WHERE chat_id=?", (chat_id,))
            self.db.execute(
                "DELETE FROM outbox WHERE subscription_id IN "
                "(SELECT id FROM subscriptions WHERE chat_id=?)",
                (chat_id,),
            )

    def migrate_chat(self, old_id: int, new_id: int):
        with self.db:
            # Prefer existing destination settings in the unlikely event of a collision.
            self.db.execute(
                "DELETE FROM subscriptions WHERE chat_id=? AND thread_id IN "
                "(SELECT thread_id FROM subscriptions WHERE chat_id=?)",
                (old_id, new_id),
            )
            self.db.execute("UPDATE subscriptions SET chat_id=? WHERE chat_id=?", (new_id, old_id))

    def collect_changes(self, schedules: dict[str, GroupSchedule]) -> int:
        count = 0
        with self.db:
            for sub in self.db.execute("SELECT * FROM subscriptions WHERE enabled=1").fetchall():
                schedule = schedules.get(sub["group_name"])
                if schedule is None:
                    continue  # Not a cancellation; source selection may have changed.
                before, after = json.loads(sub["snapshot"]), schedule.snapshot()
                if set(before) - set(after):
                    logging.getLogger(__name__).warning(
                        "Day headers disappeared for %s; keeping subscription baseline",
                        schedule.group,
                    )
                    continue
                lang = sub["language"]
                for key in sorted(set(before) | set(after)):
                    if before.get(key, []) == after.get(key, []):
                        continue
                    text = format_day(
                        schedule.group,
                        key,
                        schedule.days.get(key, ()),
                        changed=True,
                        as_html=True,
                        lang=lang,
                    )
                    if key.startswith("w:"):
                        text += "\n\n" + t(lang, "weekly_change_note")
                    self.db.execute(
                        "INSERT INTO outbox(subscription_id, parts) VALUES (?,?)",
                        (sub["id"], json.dumps(split_text(text), ensure_ascii=False)),
                    )
                    count += 1
                self.db.execute(
                    "UPDATE subscriptions SET snapshot=? WHERE id=?",
                    (json.dumps(after, ensure_ascii=False), sub["id"]),
                )
        return count

    def collect_digests(
        self, schedules: dict[str, GroupSchedule], now: datetime, default_time: time
    ) -> int:
        """Queue today+tomorrow for chats that opted in, once their own chosen time
        (or the server-wide default, if they never set one) has passed for today."""
        count = 0
        today = now.date()
        stamp = today.isoformat()
        with self.db:
            rows = self.db.execute(
                "SELECT * FROM subscriptions WHERE enabled=1 AND daily_digest=1 "
                "AND (digest_date IS NULL OR digest_date<>?)",
                (stamp,),
            ).fetchall()
            for sub in rows:
                target = _parse_hhmm(sub["digest_time"]) or default_time
                if now.time() < target:
                    continue  # This chat's chosen time hasn't arrived yet today.
                schedule = schedules.get(sub["group_name"])
                if schedule is None:
                    continue  # Wait for the group to reappear before marking today as sent.
                text = "\n\n".join(
                    (
                        format_date(schedule, today, as_html=True, lang=sub["language"]),
                        format_date(
                            schedule, today + timedelta(days=1), as_html=True, lang=sub["language"]
                        ),
                    )
                )
                self.db.execute(
                    "INSERT INTO outbox(subscription_id, parts) VALUES (?,?)",
                    (sub["id"], json.dumps(split_text(text), ensure_ascii=False)),
                )
                self.db.execute(
                    "UPDATE subscriptions SET digest_date=? WHERE id=?", (stamp, sub["id"])
                )
                count += 1
        return count

    def pending(self):
        return self.db.execute(
            "SELECT o.*, s.chat_id, s.thread_id FROM outbox o "
            "JOIN subscriptions s ON s.id=o.subscription_id WHERE s.enabled=1 ORDER BY o.id"
        ).fetchall()

    def pending_exists(self, item_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM outbox WHERE id=?", (item_id,)).fetchone() is not None

    def acknowledge_part(self, item_id: int, next_part: int, total: int):
        with self.db:
            if next_part >= total:
                self.db.execute("DELETE FROM outbox WHERE id=?", (item_id,))
            else:
                self.db.execute("UPDATE outbox SET next_part=? WHERE id=?", (next_part, item_id))

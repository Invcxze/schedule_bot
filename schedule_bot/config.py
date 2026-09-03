import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ParserConfig:
    sheet_names: tuple[str, ...]
    header_row: int = 2
    lesson_rows: int = 3
    group_pattern: str = r"^(?:[BM]\d{2}-[A-Z0-9]+-\d{2}|PHD)$"
    valid_from: date | None = None
    valid_until: date | None = None


@dataclass(frozen=True)
class Settings:
    token: str
    timezone: ZoneInfo
    poll_interval: int
    cache_ttl: int
    morning_digest_time: time
    redis_url: str
    database_path: Path
    group_admins_only: bool
    owner_user_id: int | None
    local_file: Path | None
    google_sheet_id: str | None
    private_google_sheet: bool
    credentials_path: Path | None
    parser: ParserConfig


def sheet_id_from_url(value: str) -> str:
    match = re.fullmatch(
        r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/[^\s]*)?", value
    )
    if not match or match[1] == "e":
        raise ValueError("Нужна обычная ссылка https://docs.google.com/spreadsheets/d/ID/edit")
    return match[1]


def load_settings(path: Path, *, need_token: bool = True) -> Settings:
    path = path.expanduser().resolve()
    with path.open("rb") as file:
        raw = tomllib.load(file)

    def resolve(value: str) -> Path:
        target = Path(value).expanduser()
        return target if target.is_absolute() else path.parent / target

    token = os.environ.get("BOT_TOKEN", "").strip()
    if need_token and not token:
        raise ValueError("Укажи BOT_TOKEN в .env или переменных окружения.")
    source = raw.get("source", {})
    env_url = os.environ.get("GOOGLE_SHEET_URL", "").strip()
    # Environment is the deployment-level override. This lets one immutable TOML
    # work in local/Docker environments without putting the sheet URL in the image.
    local = None if env_url else source.get("local_file")
    url = env_url or source.get("google_sheet_url")
    if bool(local) == bool(url):
        raise ValueError(
            "Укажи GOOGLE_SHEET_URL либо ровно один source.local_file/source.google_sheet_url."
        )
    parser = dict(raw.get("parser", {}))
    names = parser.get("sheet_names")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ValueError("parser.sheet_names должен содержать точные названия рабочих листов.")
    parser["sheet_names"] = tuple(names)
    for key in ("valid_from", "valid_until"):
        if parser.get(key) and not isinstance(parser[key], date):
            parser[key] = date.fromisoformat(parser[key])
    parser_config = ParserConfig(**parser)
    re.compile(parser_config.group_pattern)
    if parser_config.header_row < 1 or parser_config.lesson_rows != 3:
        raise ValueError("Поддерживается lesson_rows=3; header_row должен быть >= 1.")
    if (
        parser_config.valid_from
        and parser_config.valid_until
        and parser_config.valid_from > parser_config.valid_until
    ):
        raise ValueError("valid_from должен быть не позже valid_until.")
    interval = int(raw.get("poll_interval_seconds", 1800))
    ttl = int(raw.get("cache_ttl_seconds", 60))
    if interval < 30 or ttl < 0:
        raise ValueError("poll_interval_seconds >= 30; cache_ttl_seconds >= 0.")
    digest_raw = str(raw.get("morning_digest_time", "08:00"))
    digest_match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", digest_raw)
    if not digest_match:
        raise ValueError("morning_digest_time должен быть в формате ЧЧ:ММ (00–23:00–59).")
    digest_time = time(int(digest_match[1]), int(digest_match[2]))
    private = bool(source.get("private_google_sheet", False))
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if private and (not url or not creds):
        raise ValueError(
            "Для закрытой Google-таблицы нужны ссылка и GOOGLE_APPLICATION_CREDENTIALS."
        )
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0").strip()
    # Env takes priority, same as GOOGLE_SHEET_URL/REDIS_URL: keeps the one Telegram
    # user ID allowed to run /log out of the (possibly shared/committed-example) TOML.
    owner_env = os.environ.get("OWNER_USER_ID", "").strip()
    owner_raw = owner_env or raw.get("owner_user_id")
    try:
        owner_user_id = int(owner_raw) if owner_raw not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValueError("owner_user_id должен быть числовым Telegram user ID.") from exc
    return Settings(
        token,
        ZoneInfo(raw.get("timezone", "Europe/Moscow")),
        interval,
        ttl,
        digest_time,
        redis_url,
        resolve(raw.get("database_path", "data/bot.sqlite3")),
        bool(raw.get("group_admins_only", True)),
        owner_user_id,
        resolve(local) if local else None,
        sheet_id_from_url(url) if url else None,
        private,
        resolve(creds) if creds else None,
        parser_config,
    )

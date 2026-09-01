import asyncio
import logging
import time
from datetime import UTC, datetime

import aiohttp

from .config import Settings
from .models import GroupSchedule
from .parser import MAX_XLSX_BYTES, ParseError, parse_workbook

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


class Source:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def read(self) -> bytes:
        try:
            if self.settings.local_file:
                path = self.settings.local_file
                if path.stat().st_size > MAX_XLSX_BYTES:
                    raise SourceError("Файл больше 20 МБ.")
                return await asyncio.to_thread(path.read_bytes)
            if self.settings.private_google_sheet:
                return await asyncio.to_thread(self._read_private)
            url = (
                f"https://docs.google.com/spreadsheets/d/{self.settings.google_sheet_id}"
                "/export?format=xlsx"
            )
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise SourceError(f"Google вернул HTTP {response.status}. Проверь доступ.")
                    parts = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        parts.extend(chunk)
                        if len(parts) > MAX_XLSX_BYTES:
                            raise SourceError("Экспорт Google больше 20 МБ.")
                    data = bytes(parts)
                    if not data.startswith(b"PK"):
                        raise SourceError(
                            "Google вернул не XLSX. Нужен доступ по ссылке или service account."
                        )
                    return data
        except (OSError, aiohttp.ClientError, TimeoutError) as exc:
            # Do not expose credential-bearing HTTP URLs or local paths in chat.
            raise SourceError("Источник недоступен. Проверь файл, сеть и права чтения.") from exc

    def _read_private(self) -> bytes:
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise SourceError(
                "Не установлены зависимости Google. Выполни uv sync --locked."
            ) from exc
        try:
            credentials = Credentials.from_service_account_file(
                str(self.settings.credentials_path),
                scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
            with AuthorizedSession(credentials) as session:
                url = f"https://www.googleapis.com/drive/v3/files/{self.settings.google_sheet_id}/export"
                with session.get(
                    url,
                    params={
                        "mimeType": (
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                    },
                    timeout=60,
                    stream=True,
                ) as response:
                    if response.status_code != 200:
                        raise SourceError(f"Drive API вернул HTTP {response.status_code}.")
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        data.extend(chunk)
                        if len(data) > MAX_XLSX_BYTES:
                            raise SourceError("Экспорт Google больше 20 МБ.")
                    return bytes(data)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(
                "Не удалось прочитать закрытую таблицу через service account."
            ) from exc


class ScheduleService:
    def __init__(self, source: Source, settings: Settings):
        self.source = source
        self.settings = settings
        self.schedules: dict[str, GroupSchedule] = {}
        self.loaded_at: datetime | None = None
        self.last_error: str | None = None
        self._attempt_at = float("-inf")
        self._lock = asyncio.Lock()

    async def refresh(self, *, force: bool = False) -> dict[str, GroupSchedule]:
        async with self._lock:
            if not force and time.monotonic() - self._attempt_at < self.settings.cache_ttl:
                if self.schedules:
                    return self.schedules
                raise SourceError(self.last_error or "Расписание пока не загружено.")
            self._attempt_at = time.monotonic()
            try:
                data = await self.source.read()
                result = await asyncio.to_thread(parse_workbook, data, self.settings.parser)
                # A missing group is not a mass cancellation. Keep the entire previous dataset.
                missing = set(self.schedules) - set(result)
                if missing:
                    raise ParseError("Из источника исчезли группы: " + ", ".join(sorted(missing)))
                for group, old in self.schedules.items():
                    if set(old.days) - set(result[group].days):
                        raise ParseError(
                            f"Из сетки {group} исчезли заголовки дней. Проверь структуру."
                        )
            except Exception as exc:
                self.last_error = (
                    str(exc)
                    if isinstance(exc, (SourceError, ParseError))
                    else "Ошибка чтения XLSX."
                )
                log.warning("Schedule refresh failed (%s): %s", type(exc).__name__, self.last_error)
                if force or not self.schedules:
                    raise SourceError(self.last_error) from exc
                return self.schedules
            self.schedules = result
            self.loaded_at = datetime.now(UTC)
            self.last_error = None
            return result

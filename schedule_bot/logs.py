"""File-based log tail shared between the bot and Celery processes.

The bot (`schedule-bot run`) and the Celery worker/beat are separate processes
— separate containers in Docker — so an in-memory buffer in one is invisible
to the other. Both already mount the same data directory (see compose.yaml's
`bot-data` volume, shared with the SQLite database), so writing each
process's own rotating log file there and tailing both from `/log` gives one
view across both without a Docker socket or a new Redis channel.
"""

import logging
import logging.handlers
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_BYTES = 2_000_000
BACKUP_COUNT = 1


def log_file(database_path: Path, process: str) -> Path:
    return database_path.parent / f"{process}.log"


def attach_file_handler(logger: logging.Logger, path: Path, level: int = logging.INFO) -> None:
    """Idempotent: safe to call again (e.g. Celery re-runs its logger setup
    signal per worker subprocess) without piling up duplicate handlers."""
    if any(getattr(handler, "baseFilename", None) == str(path) for handler in logger.handlers):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(level)
    logger.addHandler(handler)


def tail(paths: list[Path], limit: int) -> str:
    """Merge the last `limit` lines across the given log files, oldest first.

    Every line starts with an ISO-like timestamp (LOG_FORMAT), so sorting the
    raw lines interleaves both processes' files in the right order; the
    `[stem]` label is added after sorting so it doesn't affect the order.
    """
    entries: list[tuple[str, str]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", errors="replace") as file:
            entries.extend((line.rstrip("\n"), path.stem) for line in file.readlines()[-limit:])
    entries.sort(key=lambda entry: entry[0])
    return "\n".join(f"[{label}] {line}" for line, label in entries[-limit:])

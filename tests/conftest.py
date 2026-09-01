from dataclasses import replace
from pathlib import Path

import pytest

from schedule_bot.config import load_settings


@pytest.fixture
def settings(tmp_path):
    # No Telegram token and no .env needed for tests.
    original = load_settings(Path(__file__).parents[1] / "config.example.toml", need_token=False)
    return replace(original, database_path=tmp_path / "state.sqlite3", cache_ttl=60)

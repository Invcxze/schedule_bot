from pathlib import Path

import pytest

from schedule_bot.config import load_settings

EXAMPLE_CONFIG = Path(__file__).parents[1] / "config.example.toml"


def test_google_sheet_env_overrides_local_file(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_SHEET_URL",
        "https://docs.google.com/spreadsheets/d/14JhWp5BPsjkuC4PFAls1yzK4pxqSMeetLdSgNqL1QM8/edit",
    )
    settings = load_settings(EXAMPLE_CONFIG, need_token=False)
    assert settings.local_file is None
    assert settings.google_sheet_id == "14JhWp5BPsjkuC4PFAls1yzK4pxqSMeetLdSgNqL1QM8"


def test_owner_user_id_defaults_to_none():
    assert load_settings(EXAMPLE_CONFIG, need_token=False).owner_user_id is None


def test_owner_user_id_from_toml(tmp_path):
    config = tmp_path / "config.toml"
    # Top-level keys must come before the first [table] header in TOML.
    config.write_text("owner_user_id = 555\n" + EXAMPLE_CONFIG.read_text())
    assert load_settings(config, need_token=False).owner_user_id == 555


def test_owner_user_id_env_overrides_toml(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    # Top-level keys must come before the first [table] header in TOML.
    config.write_text("owner_user_id = 555\n" + EXAMPLE_CONFIG.read_text())
    monkeypatch.setenv("OWNER_USER_ID", "777")
    assert load_settings(config, need_token=False).owner_user_id == 777


def test_owner_user_id_rejects_non_numeric(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('owner_user_id = "not-a-number"\n' + EXAMPLE_CONFIG.read_text())
    with pytest.raises(ValueError, match="owner_user_id"):
        load_settings(config, need_token=False)

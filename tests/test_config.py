from pathlib import Path

from schedule_bot.config import load_settings


def test_google_sheet_env_overrides_local_file(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_SHEET_URL",
        "https://docs.google.com/spreadsheets/d/14JhWp5BPsjkuC4PFAls1yzK4pxqSMeetLdSgNqL1QM8/edit",
    )
    settings = load_settings(Path(__file__).parents[1] / "config.example.toml", need_token=False)
    assert settings.local_file is None
    assert settings.google_sheet_id == "14JhWp5BPsjkuC4PFAls1yzK4pxqSMeetLdSgNqL1QM8"

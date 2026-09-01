import asyncio
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from schedule_bot.source import Source, SourceError


def test_local_source_error_is_safe(settings, tmp_path):
    source = Source(replace(settings, local_file=tmp_path / "missing.xlsx"))
    with pytest.raises(SourceError, match="недоступен") as error:
        asyncio.run(source.read())
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(
    "status,body,error",
    [
        (200, b"PKexample", None),
        (403, b"no access", "HTTP 403"),
        (200, b"<html>Sign in</html>", "не XLSX"),
    ],
)
def test_public_google_export(settings, monkeypatch, status, body, error):
    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def iter_chunked(self, size):
            yield body

    response = Response()
    response.status = status
    response.content = response

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url):
            assert url == "https://docs.google.com/spreadsheets/d/test_id/export?format=xlsx"
            return response

    monkeypatch.setattr("schedule_bot.source.aiohttp.ClientSession", lambda **kwargs: Session())
    source = Source(replace(settings, local_file=None, google_sheet_id="test_id"))
    if error:
        with pytest.raises(SourceError, match=error):
            asyncio.run(source.read())
    else:
        assert asyncio.run(source.read()) == body


def test_private_google_export_readonly_scope(settings, tmp_path):
    pytest.importorskip("google.auth")
    credentials_file = tmp_path / "service-account.json"
    # Mock the credential loader: never create/read an actual private key in this test.
    with (
        patch("google.oauth2.service_account.Credentials.from_service_account_file") as loader,
        patch("google.auth.transport.requests.AuthorizedSession") as session_class,
    ):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.return_value = [b"PK", b"example"]
        session = session_class.return_value.__enter__.return_value
        session.get.return_value = response
        source = Source(
            replace(
                settings,
                local_file=None,
                google_sheet_id="test_id",
                private_google_sheet=True,
                credentials_path=credentials_file,
            )
        )
        assert asyncio.run(source.read()) == b"PKexample"
        loader.assert_called_once_with(
            str(credentials_file), scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        assert session.get.call_args.args[0].endswith("/files/test_id/export")
        assert session.get.call_args.kwargs["params"]["mimeType"].endswith("spreadsheetml.sheet")

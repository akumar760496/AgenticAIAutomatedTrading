"""The consent flow exchanges correctly, and never leaks the app secret."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from trading_desk.adapters.clock import IST
from trading_desk.adapters.dhan.consent import ConsentError, DhanConsentFlow, TokenGrant
from trading_desk.adapters.dhan.token_store import (
    StoredTokenProvider,
    TokenExpiredError,
    load_token,
    save_token,
)

APP_SECRET = "app-secret-that-must-never-appear"
ACCESS_TOKEN = "access-token-that-must-never-appear"


def _flow(transport: httpx.MockTransport) -> DhanConsentFlow:
    return DhanConsentFlow(
        client_id="1000000001",
        app_id="app-id",
        app_secret=APP_SECRET,
        http_client=httpx.Client(transport=transport, base_url="https://auth.dhan.co"),
    )


class TestConsentSteps:
    def test_generate_consent_returns_the_consent_app_id(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"consentAppId": "consent-123"})
        )

        assert _flow(transport).generate_consent() == "consent-123"

    def test_the_app_secret_is_sent_as_a_header_not_a_query_parameter(self) -> None:
        """A secret in a query string lands in server logs and browser history."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers).get("app_secret", "")
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"consentAppId": "consent-123"})

        _flow(httpx.MockTransport(handler)).generate_consent()

        assert seen["headers"] == APP_SECRET
        assert APP_SECRET not in seen["url"]

    def test_login_url_carries_the_consent_app_id(self) -> None:
        url = DhanConsentFlow.login_url("consent-123")

        assert "consentAppId=consent-123" in url
        assert url.startswith("https://auth.dhan.co/login/")

    def test_consume_consent_returns_a_token_and_expiry(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "accessToken": ACCESS_TOKEN,
                    "expiryTime": "2026-11-15T12:37:23",
                    "dhanClientId": "1000000001",
                },
            )
        )

        grant = _flow(transport).consume_consent("token-id-abc")

        assert grant.access_token == ACCESS_TOKEN
        assert grant.expiry_time == datetime(2026, 11, 15, 12, 37, 23, tzinfo=IST)

    def test_a_missing_token_in_the_response_raises(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

        with pytest.raises(ConsentError, match="no token or expiry"):
            _flow(transport).consume_consent("token-id-abc")

    def test_an_http_error_does_not_leak_the_secret(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, text="unauthorized app")
        )

        with pytest.raises(ConsentError) as caught:
            _flow(transport).generate_consent()

        assert APP_SECRET not in str(caught.value)

    def test_repr_hides_the_token(self) -> None:
        grant = TokenGrant(ACCESS_TOKEN, datetime(2026, 11, 15, tzinfo=IST), "1000000001")

        assert ACCESS_TOKEN not in repr(grant)
        assert "***" in repr(grant)


class TestTokenStore:
    def _grant(self, expiry: datetime) -> TokenGrant:
        return TokenGrant(ACCESS_TOKEN, expiry, "1000000001")

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "dhan_token.json"
        expiry = datetime(2026, 11, 15, 12, 0, tzinfo=IST)

        save_token(self._grant(expiry), path)
        loaded = load_token(path)

        assert loaded.access_token == ACCESS_TOKEN
        assert loaded.expiry_time == expiry

    def test_the_file_is_owner_only(self, tmp_path: Path) -> None:
        """A token other accounts can read is a token leaked."""
        path = tmp_path / "dhan_token.json"

        save_token(self._grant(datetime(2026, 11, 15, tzinfo=IST)), path)

        assert oct(path.stat().st_mode)[-3:] == "600"

    def test_a_live_token_is_returned(self, tmp_path: Path) -> None:
        path = tmp_path / "dhan_token.json"
        now = datetime(2026, 10, 16, 12, 0, tzinfo=IST)
        save_token(self._grant(now + timedelta(days=5)), path)

        assert StoredTokenProvider(path, now=now)() == ACCESS_TOKEN

    def test_an_expired_token_raises_with_the_fix(self, tmp_path: Path) -> None:
        """Fail where the problem is, naming the command, not as a 401 three layers down."""
        path = tmp_path / "dhan_token.json"
        now = datetime(2026, 10, 16, 12, 0, tzinfo=IST)
        save_token(self._grant(now - timedelta(hours=1)), path)

        with pytest.raises(TokenExpiredError, match="dhan_login"):
            StoredTokenProvider(path, now=now)()

    def test_a_missing_file_names_the_command(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="dhan_login"):
            StoredTokenProvider(tmp_path / "absent.json")()

    def test_hours_remaining_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "dhan_token.json"
        now = datetime(2026, 10, 16, 12, 0, tzinfo=IST)
        save_token(self._grant(now + timedelta(hours=36)), path)

        assert StoredTokenProvider(path, now=now).expires_in_hours() == pytest.approx(36.0)

    def test_repr_hides_the_token(self, tmp_path: Path) -> None:
        assert "***" in repr(StoredTokenProvider(tmp_path / "x.json"))

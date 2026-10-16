"""The DhanHQ consent flow: API key + secret -> access token, via a browser login.

Three steps, per Dhan's auth documentation:

1. POST generate-consent with the app id and secret  -> consentAppId
2. The user opens a login URL in a browser and completes 2FA
   -> Dhan redirects to our redirect URL carrying a tokenId
3. GET consumeApp-consent with that tokenId          -> accessToken + expiryTime

Chosen over storing a PIN and TOTP seed: this keeps two-factor authentication
intact. The second factor stays something the user does in a browser, not a string
sitting on disk next to the first factor.

The token's lifetime is not documented. `expiryTime` comes back in step 3, so the
answer is measured rather than assumed - see scripts/dhan_login.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from trading_desk.adapters.clock import IST

AUTH_BASE_URL = "https://auth.dhan.co"
GENERATE_CONSENT_PATH = "/app/generate-consent"
CONSUME_CONSENT_PATH = "/app/consumeApp-consent"
LOGIN_URL_TEMPLATE = "https://auth.dhan.co/login/consentApp-login?consentAppId={consent_app_id}"

DEFAULT_TIMEOUT_SECONDS = 30.0


def _parse_ist(timestamp: str) -> datetime:
    """Parse Dhan's naive IST timestamp into a tz-aware one."""
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is not None:
        return parsed
    return parsed.replace(tzinfo=IST)


class ConsentError(RuntimeError):
    """A step of the consent flow failed. Never carries the app secret."""


@dataclass(frozen=True)
class TokenGrant:
    """An access token and when it stops working.

    Dhan sends expiryTime as a naive ISO timestamp documented as IST. The timezone
    is attached here, at the edge, so everything inland stays tz-aware (CLAUDE.md
    rule 5). A naive timestamp compared against a UTC clock is off by 5h30m, which
    would silently use an expired token for five and a half hours.
    """

    access_token: str
    expiry_time: datetime
    client_id: str

    def __repr__(self) -> str:
        return f"TokenGrant(client_id={self.client_id}, expiry_time={self.expiry_time}, token=***)"


class DhanConsentFlow:
    """Runs the three-step consent exchange."""

    def __init__(
        self,
        client_id: str,
        app_id: str,
        app_secret: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._client_id = client_id
        self._app_id = app_id
        self._app_secret = app_secret
        self._http = http_client or httpx.Client(
            base_url=AUTH_BASE_URL, timeout=DEFAULT_TIMEOUT_SECONDS
        )

    def _headers(self) -> dict[str, str]:
        return {"app_id": self._app_id, "app_secret": self._app_secret}

    def generate_consent(self) -> str:
        """Step 1. Returns a consentAppId, good for one login."""
        body = self._request("POST", GENERATE_CONSENT_PATH, {"client_id": self._client_id})
        consent_app_id = body.get("consentAppId")
        if not consent_app_id:
            raise ConsentError(f"generate-consent returned no consentAppId: {body}")
        return str(consent_app_id)

    @staticmethod
    def login_url(consent_app_id: str) -> str:
        """Step 2. The URL the user opens. Dhan redirects back with a tokenId."""
        return LOGIN_URL_TEMPLATE.format(consent_app_id=consent_app_id)

    def consume_consent(self, token_id: str) -> TokenGrant:
        """Step 3. Exchanges the tokenId from the redirect for an access token."""
        body = self._request("GET", CONSUME_CONSENT_PATH, {"tokenId": token_id})

        token = body.get("accessToken")
        expiry = body.get("expiryTime")
        if not token or not expiry:
            raise ConsentError(f"consumeApp-consent returned no token or expiry: {list(body)}")

        return TokenGrant(
            access_token=str(token),
            expiry_time=_parse_ist(str(expiry)),
            client_id=str(body.get("dhanClientId") or self._client_id),
        )

    def _request(self, method: str, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, params=params, headers=self._headers())
        except httpx.HTTPError as error:
            raise ConsentError(f"{path}: request failed ({type(error).__name__})") from error

        if response.status_code != httpx.codes.OK:
            raise ConsentError(f"{path}: HTTP {response.status_code} - {response.text[:200]}")

        body: dict[str, Any] = response.json()
        return body

    def close(self) -> None:
        self._http.close()

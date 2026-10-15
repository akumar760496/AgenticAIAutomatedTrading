"""Suite-wide guarantees.

The important one: no test may read the real .env. A test that picks up a
developer's live credentials passes on their machine, fails in CI, and - worse -
can put a real secret into test output.
"""

from __future__ import annotations

import pytest

REAL_CREDENTIAL_VARS = [
    "DHAN_CLIENT_ID",
    "DHAN_API_KEY",
    "DHAN_API_SECRET",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
]


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip real credentials from the environment for every test.

    Tests that need values set them explicitly with monkeypatch.setenv, so what a
    test depends on is visible in the test itself.
    """
    for name in REAL_CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)

"""Suite-wide guarantees.

Two things are enforced here for every test, because both are the kind of mistake
that passes on a developer's machine and fails elsewhere - or worse, passes
everywhere while quietly using real credentials and real network calls.

1. No test reads the real .env.
2. No test opens a network connection.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

REAL_CREDENTIAL_VARS = [
    "DHAN_CLIENT_ID",
    "DHAN_API_KEY",
    "DHAN_API_SECRET",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
]


class NetworkAccessError(RuntimeError):
    """Raised when a test tries to open a socket.

    If you hit this, the code under test is missing a port. Add a Protocol in
    ports/, a fake in tests/fakes/, and inject it - do not mark the test as
    integration to get around it.
    """


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip real credentials from the environment for every test.

    Tests that need values set them explicitly with monkeypatch.setenv, so what a
    test depends on is visible in the test itself.
    """
    for name in REAL_CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest) -> Iterator[None]:
    """Block outbound connections for every test not marked `integration`.

    Loopback stays open, so a local Postgres or Redis in an integration test still
    works. Everything else raises.
    """
    if request.node.get_closest_marker("integration"):
        yield
        return

    real_connect = socket.socket.connect

    # Signature mirrors socket.connect, which accepts a tuple, a path or a buffer.
    def guarded_connect(self: Any, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in {"127.0.0.1", "::1", "localhost"}:
            real_connect(self, address)
            return
        raise NetworkAccessError(
            f"A test tried to connect to {host}. Use a fake from tests/fakes/ instead."
        )

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]

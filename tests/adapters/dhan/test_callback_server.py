"""The local callback captures the tokenId and nothing else."""

from __future__ import annotations

import threading
from urllib.request import urlopen

import pytest

from trading_desk.adapters.dhan.callback_server import wait_for_token_id

PORT = 18080


@pytest.mark.integration  # binds a loopback port
def test_the_token_id_is_captured_from_the_redirect() -> None:
    captured: list[str] = []

    def serve() -> None:
        captured.append(wait_for_token_id(port=PORT, timeout=10))

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/dhan/callback?tokenId=abc-123") as response:
            body = response.read()
    finally:
        thread.join(timeout=10)

    assert captured == ["abc-123"]
    assert b"Login complete" in body

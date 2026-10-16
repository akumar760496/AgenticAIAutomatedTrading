"""Catches Dhan's redirect after the browser login.

Dhan sends the browser to the redirect URL registered with the API key, carrying
a tokenId in the query string. Rather than asking the user to copy a token out of
their address bar, a one-shot local server receives it.

It serves exactly one request and shuts down. There is no long-running listener,
so nothing is left open on the machine.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8080
DEFAULT_TIMEOUT_SECONDS = 300.0

SUCCESS_PAGE = b"""<!doctype html>
<html><body style="font-family: system-ui; padding: 3rem">
<h2>Login complete</h2>
<p>You can close this tab and return to the terminal.</p>
</body></html>"""

FAILURE_PAGE = b"""<!doctype html>
<html><body style="font-family: system-ui; padding: 3rem">
<h2>No tokenId in the redirect</h2>
<p>Check the terminal for what Dhan sent back.</p>
</body></html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the query string of the single request it serves."""

    # Class-level because BaseHTTPRequestHandler constructs a fresh instance per
    # request, so an instance attribute could not be read back by the caller.
    captured: ClassVar[dict[str, list[str]]] = {}

    def do_GET(self) -> None:
        _CallbackHandler.captured = parse_qs(urlparse(self.path).query)
        token_id = _CallbackHandler.captured.get("tokenId")

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(SUCCESS_PAGE if token_id else FAILURE_PAGE)

    def log_message(self, format: str, *args: Any) -> None:
        """Silenced: the default handler prints the full request line, tokenId included."""


def wait_for_token_id(port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Serve one request and return the tokenId Dhan sent."""
    _CallbackHandler.captured = {}
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = timeout
    try:
        server.handle_request()
    finally:
        server.server_close()

    token_ids = _CallbackHandler.captured.get("tokenId")
    if not token_ids:
        raise TimeoutError(
            f"No tokenId received on port {port}. Dhan sent: {sorted(_CallbackHandler.captured)}"
        )
    return token_ids[0]

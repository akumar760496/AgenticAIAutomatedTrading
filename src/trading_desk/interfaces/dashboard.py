"""Serving the backtest dashboard from the results directory.

A static file server rather than a web framework. The dashboard is one HTML
file and the results are one JSON file beside it, so there is no route, no
template and no state - which also means nothing to get wrong when a new
strategy appears in the results.

**Localhost only, on purpose.** It binds 127.0.0.1 rather than 0.0.0.0: this
serves a directory of trading research off a laptop, and binding every
interface would publish it to whatever network the laptop is on.
"""

from __future__ import annotations

import logging
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

DASHBOARD_PORT = 8765
LOOPBACK = "127.0.0.1"

PAGE_NAME = "index.html"
PAGE_SOURCE = Path(__file__).parent / "dashboard_page.html"


class _QuietHandler(SimpleHTTPRequestHandler):
    """Serves the results directory without logging every asset request."""

    def log_message(self, format: str, *args: object) -> None:
        log.debug("dashboard: " + format, *args)


def ensure_page(results_dir: Path) -> None:
    """Copy the page in beside the results, so one directory is all that is served.

    Rewritten every time rather than copied once: the page is source code, and
    a stale copy in `data/` that nobody thinks to update is a worse bug than
    an extra file write.
    """
    (results_dir / PAGE_NAME).write_text(PAGE_SOURCE.read_text())


def serve_dashboard(port: int = DASHBOARD_PORT, results_dir: Path | None = None) -> None:
    """Serve until interrupted."""
    directory = results_dir or Path("data/backtests")
    ensure_page(directory)

    handler = partial(_QuietHandler, directory=str(directory))
    address = f"http://{LOOPBACK}:{port}/"
    with ThreadingHTTPServer((LOOPBACK, port), handler) as server:
        print(f"Dashboard on {address}  (ctrl-c to stop)")
        webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")

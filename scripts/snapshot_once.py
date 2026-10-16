"""Run one chain snapshot against live Dhan data. Wiring, not logic.

This is the entry point a scheduler will call in step 1.1c. Everything it does is
assembled from ports, so the same snapshotter runs against fakes in tests.

    uv run python -m scripts.snapshot_once
"""

from __future__ import annotations

import sys

from trading_desk.adapters.clock import SystemClock
from trading_desk.adapters.dhan.chain import DhanOptionChainAdapter
from trading_desk.adapters.dhan.client import DhanClient
from trading_desk.adapters.dhan.instruments import DhanInstrumentMaster
from trading_desk.adapters.dhan.login import ensure_valid_token, hours_remaining
from trading_desk.adapters.dhan.token_store import StoredTokenProvider
from trading_desk.adapters.notify.logging_notifier import LoggingNotifier
from trading_desk.adapters.store.parquet_chain import ParquetChainArchive
from trading_desk.application.snapshotter import ChainSnapshotter
from trading_desk.application.universe import load_universe_request, resolve_universe
from trading_desk.infra.config import load_app_settings, load_dhan_settings
from trading_desk.infra.logging import configure_logging, register_secret


def main() -> int:
    dhan = load_dhan_settings()

    # Authenticate on startup. Logs in through the browser only when there is no
    # usable token, so a scheduled run with a live token never blocks on a human.
    grant = ensure_valid_token(dhan, announce=print)
    print(f"Authenticated. Token valid for {hours_remaining(grant):.1f} more hours.")
    token_provider = StoredTokenProvider()

    register_secret(token_provider())
    register_secret(dhan.api_secret)
    configure_logging(load_app_settings().log_level)

    clock = SystemClock()
    client = DhanClient(client_id=dhan.client_id, token_provider=token_provider)
    snapshotter = ChainSnapshotter(
        chain_source=DhanOptionChainAdapter(client, clock),
        archive=ParquetChainArchive(),
        notifier=LoggingNotifier(clock),
        clock=clock,
    )

    catalogue = DhanInstrumentMaster().option_underlyings(clock.utc_now().date())
    universe = resolve_universe(load_universe_request(), catalogue)
    if universe.unavailable:
        print(f"Not capturing (no listed options): {', '.join(universe.unavailable)}")

    try:
        health = snapshotter.run(list(universe.all_instruments()))
    finally:
        client.close()

    print(health.summary())
    for failure in health.failed:
        print(f"  FAILED {failure.symbol}: {failure.error}")
    return 0 if health.is_complete else 1


if __name__ == "__main__":
    sys.exit(main())

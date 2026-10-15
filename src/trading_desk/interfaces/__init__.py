"""Entry points: CLI, FastAPI app, Telegram handlers.

The outermost layer. Wires concrete adapters into application use cases - this is
where backtest/paper/live is decided, by injection rather than a mode flag.
"""

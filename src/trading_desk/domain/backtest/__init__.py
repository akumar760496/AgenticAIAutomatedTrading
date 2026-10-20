"""Backtesting: strategies, the simulation engine, and failure diagnosis.

Pure `domain/` code. Nothing here reads a database, a file or a clock - the
caller hands it bars and gets trades back, which is what makes a strategy
testable without a market.
"""

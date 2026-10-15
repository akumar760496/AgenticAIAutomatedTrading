"""Protocols for everything outside the application.

A port says what we need (fetch a chain, send a message, read bars). The adapter
says how. Application code depends on the port, never the adapter, so tests can
swap in a fake and the suite never touches the network.
"""

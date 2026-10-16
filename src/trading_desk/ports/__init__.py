"""Protocols for everything outside the application.

A port says what we need (fetch a chain, send a message, read bars). The adapter
says how. Application code depends on the port, never the adapter, so tests can
swap in a fake and the suite never touches the network.

Note what is absent: there is no order-placing port. v1 reads from the broker and
nothing more. The write path arrives in v2 as its own port with its own approval
gate, so until then no code path exists that could place an order.
"""

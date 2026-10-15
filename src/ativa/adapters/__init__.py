"""Implementations of ports: Dhan, news sources, Telegram, Postgres, LLM providers.

The only layer allowed to know about the outside world. Raw payloads are mapped to
domain models here and nothing typed Any crosses out of this layer.
"""

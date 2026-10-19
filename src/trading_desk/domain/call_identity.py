"""One definition of "the same model call".

Two different things need it and they must agree. The recorded-cassette client
(`adapters/llm/recorded.py`) uses it to find a stored reply on disk; the cycle
response cache uses it to replay a reply after a crash. If those two hashed
different things, a resumed cycle would silently pay again for a call a
cassette would have considered identical.

**The prompt is part of the identity, not just the node name.** Keying on the
node would keep replaying a stale answer after the prompt had been rewritten,
and nothing would look wrong - the run would be cheap, fast and answering the
old question.
"""

from __future__ import annotations

import hashlib

# Enough to make a collision not worth thinking about, short enough to read in
# a log line. 16 hex characters is 64 bits.
KEY_LENGTH = 16


def call_key(model: str, system_prompt: str, user_content: str) -> str:
    """A short, stable name for one exact call."""
    digest = hashlib.sha256(f"{model}\n{system_prompt}\n{user_content}".encode()).hexdigest()
    return f"{model}-{digest[:KEY_LENGTH]}"

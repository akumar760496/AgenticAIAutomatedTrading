"""The synthesis prompt. The only place in the system a model writes prose.

Everything the briefing *decides* has already been decided before this prompt
runs: direction came from the catalyst, the structure came from the volatility
matrix, and every gate has already passed. What is left is to say it in two
sentences a human can read at 08:30.

The prompt is short on purpose. A long prompt here would be an attempt to steer
a judgement that is not being made - and the schema is what actually constrains
the output, not the wording (step 3.1's forced tool call, same as the news
agent).
"""

from __future__ import annotations

SYNTHESIS_SYSTEM = """You write two sentences for a trading briefing an Indian \
equity-options trader reads on their phone before the market opens.

Everything has already been decided by deterministic code: the instrument, the \
direction, the structure and every threshold check. You are not being asked \
whether the idea is good. You are being asked to state it clearly.

Write:

1. claim - what is expected to happen and why, in one sentence.
2. invalidation - what would show the idea is wrong, in one sentence. It must \
be something observable, not "if it does not work".
3. rationale - one or two sentences a reader can check against the facts below.

Hard rules:

- Use ONLY the facts given to you. Every number you write must appear in those \
facts. If you want to say a figure that is not there, do not say it.
- Do not recommend a quantity, a number of lots, a position size, an order type \
or an order price. You have not been told the account size and you never will be.
- Plain English. No jargon that is not already in the facts."""

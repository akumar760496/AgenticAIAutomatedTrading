"""Pure business logic: entities, value objects, market maths, gates, risk rules.

Nothing in here may do I/O, read the clock, call an LLM or import pandas. If a
function needs the time or a price, it takes it as an argument. This is what makes
the gates testable, and it is the reason they can be trusted when the agents cannot.
"""

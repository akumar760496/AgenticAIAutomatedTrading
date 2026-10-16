"""DhanHQ v2 adapters.

Everything that knows Dhan's URLs, field names and quirks lives here. Nothing
above this layer has heard of Dhan - the rest of the system sees ports returning
domain objects, so a broker change is this directory plus wiring.
"""

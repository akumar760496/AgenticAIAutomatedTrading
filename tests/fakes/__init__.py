"""In-memory stand-ins for every external dependency.

Each fake returns what the test gave it and nothing else. A fake that invents
plausible data tests the fake rather than the code, so these refuse rather than
guess: ask FakeMarketData for a quote nobody set and it raises.

Several carry a deliberate failure switch (fail_next_fetch, fail_next_send)
because the error paths matter as much as the happy ones - fail-closed behaviour
is only real if it is tested.
"""

from tests.fakes.bar_store import FakeBarStore
from tests.fakes.broker import FakeBrokerRead
from tests.fakes.chain_archive import FakeChainArchive
from tests.fakes.clock import FrozenClock
from tests.fakes.expiry_store import FakeExpiryStore
from tests.fakes.history import FakeHistory
from tests.fakes.journal import FakeJournal
from tests.fakes.llm import FakeLlm
from tests.fakes.market_data import FakeMarketData
from tests.fakes.news import FakeFilings, FakeNews
from tests.fakes.news_store import FakeNewsStore
from tests.fakes.notifier import FakeNotifier
from tests.fakes.option_chain import FakeOptionChain

__all__ = [
    "FakeBarStore",
    "FakeBrokerRead",
    "FakeChainArchive",
    "FakeExpiryStore",
    "FakeFilings",
    "FakeHistory",
    "FakeJournal",
    "FakeLlm",
    "FakeMarketData",
    "FakeNews",
    "FakeNewsStore",
    "FakeNotifier",
    "FakeOptionChain",
    "FrozenClock",
]

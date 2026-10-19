"""The Fundamental agent, over filings a test supplies.

The two tests that matter are the ones about silence: an absence of filings
must not read as a clean record, and a clean record must still carry the
pledge blind spot. A veto source that reports "nothing found" when it never
looked is worse than no veto source at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.fakes import FakeNewsStore
from trading_desk.agents.fundamental.agent import PLEDGE_BLIND_SPOT, FundamentalAgent
from trading_desk.domain.governance import CorporateAction, GovernanceFlag
from trading_desk.domain.news import Filing

AS_OF = date(2026, 10, 19)


def _filing(filing_id: str, subject: str, *, days_ago: int = 5, symbol: str = "RELIANCE") -> Filing:
    filed_at = datetime(2026, 10, 19, 6, 0, tzinfo=UTC).replace(day=19 - days_ago)
    return Filing(
        filing_id=filing_id,
        symbol=symbol,
        subject=subject,
        body=f"{symbol} has informed the Exchange about {subject}.",
        url=f"https://nsearchives.nseindia.com/{filing_id}.pdf",
        filed_at=filed_at,
        ingested_at=filed_at,
    )


def _agent(*filings: Filing) -> FundamentalAgent:
    store = FakeNewsStore()
    store.save_filings(filings)
    return FundamentalAgent(store)


class TestVetoes:
    def test_a_regulatory_order_vetoes(self) -> None:
        view = _agent(_filing("f1", "Action(s) taken or orders passed")).assess("RELIANCE", AS_OF)

        assert not view.finding.is_clear
        assert view.vetoing_flags == (GovernanceFlag.REGULATORY_ACTION,)
        assert "REGULATORY_ACTION" in view.finding.vetoes[0]

    def test_a_management_exit_is_a_note_not_a_veto(self) -> None:
        """It is a tradable catalyst. Vetoing it would make Setup A unable to
        trade the catalyst type it lists."""
        view = _agent(_filing("f1", "Resignation of Director/KMP/SMP")).assess("RELIANCE", AS_OF)

        assert view.finding.is_clear
        assert view.governance_flags == ("MANAGEMENT_EXIT",)
        assert view.finding.notes

    def test_three_resignations_are_one_flag(self) -> None:
        """Staff turnover is not three separate problems."""
        view = _agent(
            _filing("f1", "Resignation", days_ago=3),
            _filing("f2", "Resignation", days_ago=5),
            _filing("f3", "Cessation", days_ago=7),
        ).assess("RELIANCE", AS_OF)

        assert view.governance_flags == ("MANAGEMENT_EXIT",)

    def test_a_routine_filing_vetoes_nothing(self) -> None:
        view = _agent(_filing("f1", "Investor Presentation")).assess("RELIANCE", AS_OF)

        assert view.finding.is_clear
        assert view.governance_flags == ()


class TestWhatItCannotSee:
    def test_the_pledge_blind_spot_is_reported_on_every_call(self) -> None:
        """Including - especially including - a clean one."""
        view = _agent(_filing("f1", "Investor Presentation")).assess("RELIANCE", AS_OF)

        assert PLEDGE_BLIND_SPOT in view.finding.unavailable

    def test_no_filings_is_reported_as_missing_data_not_as_clean(self) -> None:
        view = _agent().assess("RELIANCE", AS_OF)

        assert view.filings_read == 0
        assert view.finding.is_clear  # no veto, because nothing was found...
        assert any("absence of data" in item for item in view.finding.unavailable)

    def test_another_company_s_filings_are_not_read(self) -> None:
        view = _agent(
            _filing("f1", "Fraud/Default/Arrest", symbol="OTHERCO"),
        ).assess("RELIANCE", AS_OF)

        assert view.filings_read == 0
        assert view.finding.is_clear


class TestCorporateActions:
    def test_a_bonus_is_surfaced_because_it_breaks_the_price_series(self) -> None:
        view = _agent(_filing("f1", "Bonus")).assess("RELIANCE", AS_OF)

        assert [item.action for item in view.corporate_actions] == [CorporateAction.BONUS]
        assert view.finding.is_clear  # a bonus is not a governance problem

    def test_filings_outside_the_lookback_are_not_read(self) -> None:
        store = FakeNewsStore()
        store.save_filings([_filing("f1", "Fraud/Default/Arrest", days_ago=5)])
        # A one-day lookback excludes a five-day-old filing.
        view = FundamentalAgent(store, lookback_days=1).assess("RELIANCE", AS_OF)

        assert view.filings_read == 0


def test_measurements_carry_the_counts_not_a_verdict() -> None:
    view = _agent(
        _filing("f1", "Corporate Insolvency Resolution Process"),
        _filing("f2", "Dividend"),
    ).assess("RELIANCE", AS_OF)

    assert view.finding.measurements["filings_read"] == "2"
    assert view.finding.measurements["vetoing_flags"] == "1"
    assert "PASS" not in str(view.finding.measurements)

"""Routing filings by their exchange subject line.

The test that matters most is `test_an_undetermined_subject_is_not_routine`:
collapsing "needs reading" into NONE would silently discard every earnings
announcement in the archive, and nothing would report it.
"""

from __future__ import annotations

from trading_desk.domain.catalysts import CatalystType
from trading_desk.domain.filing_subjects import needs_reading, route


class TestSubjectsThatDecide:
    def test_a_credit_rating_filing_needs_no_model(self) -> None:
        assert route("Credit Rating") is CatalystType.CREDIT_RATING_ACTION

    def test_a_resignation_is_a_management_exit(self) -> None:
        assert route("Resignation of Director/KMP/SMP") is CatalystType.MANAGEMENT_EXIT

    def test_an_order_win_is_recognised_under_both_nse_spellings(self) -> None:
        assert route("Bagging/Receiving of orders/contracts") is CatalystType.LARGE_ORDER_WIN
        assert route("Awarding of order(s)/contract(s)") is CatalystType.LARGE_ORDER_WIN

    def test_compliance_paperwork_is_decided_routine(self) -> None:
        subject = "Certificate under SEBI (Depositories and Participants) Regulations, 2018"
        assert route(subject) is CatalystType.NONE

    def test_whitespace_differences_do_not_change_the_route(self) -> None:
        assert route("  Credit   Rating  ") is CatalystType.CREDIT_RATING_ACTION


class TestTheThreeWayAnswer:
    def test_an_undetermined_subject_is_not_routine(self) -> None:
        """ "Outcome of Board Meeting" might be an earnings beat, a dividend, a
        fundraise or nothing. Marking it NONE would discard every earnings
        announcement in the archive and report nothing."""
        assert route("Outcome of Board Meeting") is None
        assert needs_reading("Outcome of Board Meeting")

    def test_a_subject_we_have_never_seen_needs_reading_rather_than_guessing(self) -> None:
        assert route("Some New Category NSE Invented Last Tuesday") is None

    def test_none_and_needs_reading_are_distinguishable(self) -> None:
        assert route("Investor Presentation") is CatalystType.NONE
        assert not needs_reading("Investor Presentation")
        assert needs_reading("General Updates")


class TestExchangeQueriesAreNotCatalysts:
    def test_a_volume_spurt_query_is_routine_not_news(self) -> None:
        """The exchange asking why a stock moved. The move came first, so
        treating it as a catalyst inverts cause and effect."""
        assert route("Spurt in Volume") is CatalystType.NONE
        assert route("Price movement") is CatalystType.NONE

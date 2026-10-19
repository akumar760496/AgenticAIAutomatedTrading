"""The spend ledger in Postgres. Insert only.

The month boundary is the only interesting part. `spend_in_month` sums rows
whose `called_at`, converted to IST, falls in the given calendar month. Doing
the conversion in SQL rather than in Python means the sum is one query rather
than a fetch-and-filter, and it stays correct as the table grows.

Append-only is enforced by a trigger in migration 0006, not by this class. If
this adapter is ever wrong, the database still refuses.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from trading_desk.domain.llm_cost import ModelTier
from trading_desk.domain.values import Money
from trading_desk.infra.db import session_scope
from trading_desk.ports.llm_ledger import LedgerEntry

IST = "Asia/Kolkata"

INSERT_CALL = text(
    """
    INSERT INTO llm_calls (
        cycle_id, node, tier, model, input_tokens, output_tokens, cost_paise, called_at
    ) VALUES (
        :cycle_id, :node, :tier, :model, :input_tokens, :output_tokens, :cost_paise, :called_at
    )
    """
)

# COALESCE so an empty month reads as zero rather than NULL. A NULL here would
# propagate into the cap comparison and refuse every call for the wrong reason.
SELECT_MONTH_SPEND = text(
    f"""
    SELECT COALESCE(SUM(cost_paise), 0) AS total
    FROM llm_calls
    WHERE EXTRACT(YEAR  FROM called_at AT TIME ZONE '{IST}') = :year
      AND EXTRACT(MONTH FROM called_at AT TIME ZONE '{IST}') = :month
    """
)

SELECT_CYCLE = text(
    """
    SELECT cycle_id, node, tier, model, input_tokens, output_tokens, cost_paise, called_at
    FROM llm_calls
    WHERE cycle_id = :cycle_id
    ORDER BY called_at, call_id
    """
)


class PostgresLlmLedger:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def record(self, entry: LedgerEntry) -> None:
        with session_scope(self._sessions) as session:
            session.execute(
                INSERT_CALL,
                {
                    "cycle_id": entry.cycle_id,
                    "node": entry.node,
                    "tier": entry.tier.value,
                    "model": entry.model,
                    "input_tokens": entry.input_tokens,
                    "output_tokens": entry.output_tokens,
                    "cost_paise": entry.cost.paise,
                    "called_at": entry.called_at,
                },
            )

    def spend_in_month(self, year: int, month: int) -> Money:
        with session_scope(self._sessions) as session:
            total = session.execute(SELECT_MONTH_SPEND, {"year": year, "month": month}).scalar_one()
        return Money(paise=int(total))

    def entries_for_cycle(self, cycle_id: str) -> list[LedgerEntry]:
        with session_scope(self._sessions) as session:
            rows = session.execute(SELECT_CYCLE, {"cycle_id": cycle_id}).mappings().all()
        return [_to_entry(dict(row)) for row in rows]


def _to_entry(row: dict[str, object]) -> LedgerEntry:
    called_at = row["called_at"]
    if not isinstance(called_at, datetime):
        raise TypeError(f"called_at came back as {type(called_at).__name__}, expected datetime")
    return LedgerEntry(
        cycle_id=str(row["cycle_id"]),
        node=str(row["node"]),
        tier=ModelTier(str(row["tier"])),
        model=str(row["model"]),
        input_tokens=int(str(row["input_tokens"])),
        output_tokens=int(str(row["output_tokens"])),
        cost=Money(paise=int(str(row["cost_paise"]))),
        called_at=called_at,
    )

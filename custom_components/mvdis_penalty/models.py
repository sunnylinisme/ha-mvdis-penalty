"""Data models for Taiwan MVDIS Penalty."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Penalty:
    """One unpaid penalty returned by MVDIS."""

    key: str
    fields: dict[str, str]
    amount: int | None = None
    summary_text: str | None = None

    @property
    def summary(self) -> str:
        """Return a privacy-conscious human-readable summary."""
        if self.summary_text:
            return self.summary_text
        preferred = (
            "違規日",
            "違規日期",
            "違規事實",
            "違規地點",
            "應繳金額",
            "罰鍰金額",
        )
        values = [self.fields[name] for name in preferred if self.fields.get(name)]
        if values:
            return "｜".join(values)
        return "｜".join(list(self.fields.values())[:3])


@dataclass(frozen=True, slots=True)
class MvdisData:
    """Normalized MVDIS query result."""

    penalties: tuple[Penalty, ...] = field(default_factory=tuple)
    checked_at: datetime | None = None
    status: str = "ok"

    @property
    def count(self) -> int:
        """Return the number of unpaid penalties."""
        return len(self.penalties)

    @property
    def total_amount(self) -> int:
        """Return the total amount when exposed by the result page."""
        return sum(item.amount or 0 for item in self.penalties)

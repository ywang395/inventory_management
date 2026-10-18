"""Plain data types shared by the inventory logic.

All quantities are whole numbers in the item's smallest unit (e.g. bags of lids).
The dataclasses are frozen: functions return new objects instead of changing old ones,
which keeps the logic easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class CodeAction(StrEnum):
    TAKE = "take"
    RESTOCK = "restock"


class ScanAction(StrEnum):
    SCAN = "scan"
    UNDO = "undo"


class LogAction(StrEnum):
    TAKE = "take"
    RESTOCK = "restock"
    UNDO = "undo"
    CORRECTION = "correction"


class OutcomeStatus(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    NOTHING_TO_UNDO = "nothing_to_undo"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Item:
    item_id: str
    name: str
    smallest_unit: str
    has_expiry: bool
    category: str = ""
    reorder_point: int = 0
    lead_time_days: int = 0


@dataclass(frozen=True)
class Code:
    """A scannable code. `quantity` is signed: negative for take, positive for restock."""

    code: str
    item_id: str
    quantity: int
    action: CodeAction

    def __post_init__(self) -> None:
        if self.action is CodeAction.TAKE and self.quantity >= 0:
            raise ValueError(f"Take code {self.code} must have a negative quantity")
        if self.action is CodeAction.RESTOCK and self.quantity <= 0:
            raise ValueError(f"Restock code {self.code} must have a positive quantity")


@dataclass(frozen=True)
class Batch:
    item_id: str
    batch_id: str
    quantity: int
    received_on: date
    expires_on: date | None = None


@dataclass(frozen=True)
class ScanRequest:
    """One scan sent by a station (body of POST /scan)."""

    code: str
    action: ScanAction
    staff: str
    station: str
    scanned_at: datetime

    def __post_init__(self) -> None:
        if not self.station:
            raise ValueError("station is required")
        if self.scanned_at.tzinfo is None:
            raise ValueError("scanned_at must include a time zone")


@dataclass(frozen=True)
class Allocation:
    """How much one log entry changed one batch (signed, smallest units)."""

    batch_id: str
    change: int


@dataclass(frozen=True)
class LogEntry:
    """One append-only ScanLog row.

    A single scan can touch several batches (FIFO), so the per-batch changes live in
    `allocations`. `shortfall` is how many units were asked for but not in stock.
    """

    entry_id: str
    item_id: str
    timestamp: datetime
    action: LogAction
    code: str
    staff: str
    station: str
    allocations: tuple[Allocation, ...]
    shortfall: int = 0
    undoes: str | None = None  # entry_id of the entry this undo reverses

    @property
    def change(self) -> int:
        """Total change actually applied to stock."""
        return sum(a.change for a in self.allocations)


@dataclass(frozen=True)
class ScanOutcome:
    """What the handler should do: write `entry` and the updated `batches`, or nothing."""

    status: OutcomeStatus
    entry: LogEntry | None = None
    batches: tuple[Batch, ...] = ()  # only batches whose quantity changed or were created
    reason: str = ""

    @property
    def needs_count(self) -> bool:
        """True when stock was clamped at 0, so the item should be counted by hand."""
        return self.entry is not None and self.entry.shortfall > 0

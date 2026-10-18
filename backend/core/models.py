"""Plain data types shared by the inventory logic.

All quantities are whole numbers in the item's smallest unit (e.g. bags of lids).
The dataclasses are frozen: functions return new objects instead of changing old ones,
which keeps the logic easy to test.
"""

from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class Item:
    item_id: str
    name: str
    smallest_unit: str
    stock: int = 0  # current stock in smallest units, never below 0
    needs_count: bool = False  # set when a scan asked for more than was in stock
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
class LogEntry:
    """One append-only ScanLog row."""

    entry_id: str
    item_id: str
    timestamp: datetime
    action: LogAction
    code: str
    staff: str
    station: str
    change: int  # change actually applied to stock (signed)
    shortfall: int = 0  # units asked for but not in stock
    undoes: str | None = None  # entry_id of the entry this undo reverses


@dataclass(frozen=True)
class ScanOutcome:
    """What the handler should save: the new log entry and the updated item, or nothing."""

    status: OutcomeStatus
    entry: LogEntry | None = None
    item: Item | None = None

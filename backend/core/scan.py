"""Turn a scan (or an undo) into a log entry plus the updated item to save.

Handlers look up the code, the item and the station's last log entry, call these
functions, and then write the outcome to DynamoDB. Nothing here talks to AWS.
"""

from dataclasses import replace
from datetime import UTC, datetime

from core.codes import UNDO_CODE
from core.models import (
    Code,
    CodeAction,
    Item,
    LogAction,
    LogEntry,
    OutcomeStatus,
    ScanOutcome,
    ScanRequest,
)


def scan_id(station: str, scanned_at: datetime) -> str:
    """Idempotency key: a scan resent by the offline queue gets the same id again.

    The time is converted to UTC, so 10:00+02:00 and 08:00Z give the same key.
    """
    if scanned_at.tzinfo is None:
        raise ValueError("scanned_at must include a time zone")
    utc = scanned_at.astimezone(UTC)
    return f"{station}#{utc.isoformat(timespec='microseconds')}"


def clamp_stock(stock: int, change: int) -> tuple[int, int]:
    """Return (new_stock, shortfall). Stock never goes below 0; the missing units
    are the shortfall."""
    new_stock = stock + change
    if new_stock < 0:
        return 0, -new_stock
    return new_stock, 0


def process_scan(
    request: ScanRequest,
    code: Code,
    item: Item,
    *,
    already_seen: bool = False,
) -> ScanOutcome:
    """Add or deduct a code's quantity from the item's stock.

    Set `already_seen` when this station + scanned_at was processed before.
    """
    if already_seen:
        return ScanOutcome(OutcomeStatus.DUPLICATE)
    if code.item_id != item.item_id:
        raise ValueError(f"Code {code.code} belongs to {code.item_id}, not {item.item_id}")

    action = LogAction.TAKE if code.action is CodeAction.TAKE else LogAction.RESTOCK
    return _apply(request, item, change=code.quantity, action=action, code=code.code)


def build_undo(
    request: ScanRequest,
    last_entry: LogEntry | None,
    item: Item | None,
    *,
    already_seen: bool = False,
) -> ScanOutcome:
    """Reverse the station's most recent log entry by adding an opposite entry.

    `last_entry` is the newest entry from this station and `item` is its item (both
    None if the station has no entries yet). If the newest entry is already an undo,
    there is nothing to reverse, so pressing INV-UNDO twice does not reach older scans.
    """
    if already_seen:
        return ScanOutcome(OutcomeStatus.DUPLICATE)
    if last_entry is None or last_entry.action not in (LogAction.TAKE, LogAction.RESTOCK):
        return ScanOutcome(OutcomeStatus.NOTHING_TO_UNDO)
    if last_entry.station != request.station:
        raise ValueError("A station can only undo its own scans")
    if item is None or item.item_id != last_entry.item_id:
        raise ValueError(f"Undo needs item {last_entry.item_id}")

    # Reverse what was actually applied, not what the code asked for (it may have been clamped).
    return _apply(
        request,
        item,
        change=-last_entry.change,
        action=LogAction.UNDO,
        code=UNDO_CODE,
        undoes=last_entry.entry_id,
    )


def _apply(
    request: ScanRequest,
    item: Item,
    *,
    change: int,
    action: LogAction,
    code: str,
    undoes: str | None = None,
) -> ScanOutcome:
    new_stock, shortfall = clamp_stock(item.stock, change)
    updated_item = replace(item, stock=new_stock, needs_count=item.needs_count or shortfall > 0)
    entry = LogEntry(
        entry_id=scan_id(request.station, request.scanned_at),
        item_id=item.item_id,
        timestamp=request.scanned_at.astimezone(UTC),
        action=action,
        code=code,
        staff=request.staff,
        station=request.station,
        change=new_stock - item.stock,
        shortfall=shortfall,
        undoes=undoes,
    )
    return ScanOutcome(OutcomeStatus.APPLIED, entry=entry, item=updated_item)

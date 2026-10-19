"""Turn a scan (or an undo) into a log entry plus the updated item to save.

Handlers look up the shop settings, code, item, staff badge and today's log entries,
call these functions, and then write the outcome to DynamoDB. Nothing here talks to AWS.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime

from core.codes import normalize_code
from core.models import (
    Code,
    CodeAction,
    Item,
    LogAction,
    LogEntry,
    OutcomeStatus,
    ScanOutcome,
    ScanRequest,
    ShopSettings,
    Staff,
)
from core.shop import same_shop_day, staff_problem


def format_utc(moment: datetime) -> str:
    """One fixed text format for times in keys, e.g. 2026-10-17T17:00:00.000000+00:00.

    Always UTC with microseconds, so keys sort by time and the same moment always
    gives the same text.
    """
    if moment.tzinfo is None:
        raise ValueError("time must include a time zone")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def scan_id(station: str, scanned_at: datetime) -> str:
    """Idempotency key: a scan resent by the offline queue gets the same id again.

    The time is converted to UTC, so 10:00+02:00 and 08:00Z give the same key.
    """
    return f"{station}#{format_utc(scanned_at)}"


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
    settings: ShopSettings,
    staff: Staff | None = None,
    already_seen: bool = False,
) -> ScanOutcome:
    """Add or deduct a code's quantity from the item's stock.

    `staff` is the Staff record for the request's badge (only needed when the shop
    requires badges). Set `already_seen` when this station + scanned_at was processed
    before.
    """
    if already_seen:
        return ScanOutcome(OutcomeStatus.DUPLICATE)
    if problem := staff_problem(settings, request.staff, staff):
        return ScanOutcome(OutcomeStatus.REJECTED, reason=problem)
    if code.item_id != item.item_id:
        raise ValueError(f"Code {code.code} belongs to {code.item_id}, not {item.item_id}")

    action = LogAction.TAKE if code.action is CodeAction.TAKE else LogAction.RESTOCK
    return _apply(request, item, change=code.quantity, action=action, code=code.code)


def find_undo_target(
    request: ScanRequest,
    entries: Iterable[LogEntry],
    settings: ShopSettings,
) -> LogEntry | None:
    """Pick the scan that an UNDO + code should reverse, or None if there is none.

    The target is the most recent scan of exactly that code that:
    - was made at this station, today (in the shop's time zone), before the undo
    - hasn't been undone yet
    - was made by the same staff member, if the shop has `undo_own_scans_only` on

    `entries` should be this station's log entries for today, including earlier undos
    (so we know which scans are already undone).
    """
    entries = list(entries)
    already_undone = {e.undoes for e in entries if e.action is LogAction.UNDO}
    wanted_code = normalize_code(request.code)

    candidates = [
        e
        for e in entries
        if e.action in (LogAction.TAKE, LogAction.RESTOCK)
        and e.code == wanted_code
        and e.station == request.station
        and e.timestamp < request.scanned_at
        and same_shop_day(e.timestamp, request.scanned_at, settings)
        and e.entry_id not in already_undone
        and (not settings.undo_own_scans_only or e.staff == request.staff)
    ]
    return max(candidates, key=lambda e: e.timestamp, default=None)


def build_undo(
    request: ScanRequest,
    target: LogEntry | None,
    item: Item | None,
    *,
    settings: ShopSettings,
    staff: Staff | None = None,
    already_seen: bool = False,
) -> ScanOutcome:
    """Reverse `target` (from `find_undo_target`) by adding an opposite log entry.

    `item` is the target's item (None when there is no target).
    """
    if already_seen:
        return ScanOutcome(OutcomeStatus.DUPLICATE)
    if problem := staff_problem(settings, request.staff, staff):
        return ScanOutcome(OutcomeStatus.REJECTED, reason=problem)
    if target is None:
        return ScanOutcome(OutcomeStatus.NOTHING_TO_UNDO, reason=_nothing_to_undo(request))
    if target.station != request.station:
        raise ValueError("A station can only undo its own scans")
    if item is None or item.item_id != target.item_id:
        raise ValueError(f"Undo needs item {target.item_id}")

    # Reverse what was actually applied, not what the code asked for (it may have been clamped).
    return _apply(
        request,
        item,
        change=-target.change,
        action=LogAction.UNDO,
        code=target.code,
        undoes=target.entry_id,
    )


def _nothing_to_undo(request: ScanRequest) -> str:
    who = f" for {request.staff}" if request.staff else ""
    return f"Nothing to undo{who} today."


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

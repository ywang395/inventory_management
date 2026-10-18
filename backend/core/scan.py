"""Turn a scan (or an undo) into a log entry plus the batch changes to save.

Handlers look up the code, item, batches and the station's last log entry, call these
functions, and then write the outcome to DynamoDB. Nothing here talks to AWS.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from core.codes import UNDO_CODE
from core.fifo import deduct_fifo
from core.models import (
    Allocation,
    Batch,
    Code,
    CodeAction,
    Item,
    LogAction,
    LogEntry,
    OutcomeStatus,
    ScanOutcome,
    ScanRequest,
)

# Items without an expiry date keep all their stock in one batch with this id.
DEFAULT_BATCH_ID = "default"


def scan_id(station: str, scanned_at: datetime) -> str:
    """Idempotency key: a scan resent by the offline queue gets the same id again.

    The time is converted to UTC, so 10:00+02:00 and 08:00Z give the same key.
    """
    if scanned_at.tzinfo is None:
        raise ValueError("scanned_at must include a time zone")
    utc = scanned_at.astimezone(UTC)
    return f"{station}#{utc.isoformat(timespec='microseconds')}"


@dataclass(frozen=True)
class AppliedChanges:
    batches: tuple[Batch, ...]  # only batches whose quantity changed
    allocations: tuple[Allocation, ...]  # changes actually applied
    shortfall: int  # units missing because a batch was clamped at 0


def apply_allocations(
    batches: Iterable[Batch], allocations: Iterable[Allocation]
) -> AppliedChanges:
    """Apply signed changes to batches, never letting a batch go below 0."""
    current = {b.batch_id: b for b in batches}
    changed: dict[str, Batch] = {}
    applied: list[Allocation] = []
    shortfall = 0

    for alloc in allocations:
        batch = current.get(alloc.batch_id)
        if batch is None:
            raise ValueError(f"Unknown batch {alloc.batch_id}")

        new_quantity = batch.quantity + alloc.change
        if new_quantity < 0:
            shortfall += -new_quantity
            new_quantity = 0

        actual = new_quantity - batch.quantity
        if actual != 0:
            updated = replace(batch, quantity=new_quantity)
            current[batch.batch_id] = updated
            changed[batch.batch_id] = updated
            applied.append(Allocation(batch.batch_id, actual))

    return AppliedChanges(tuple(changed.values()), tuple(applied), shortfall)


def process_scan(
    request: ScanRequest,
    code: Code,
    item: Item,
    batches: Iterable[Batch],
    *,
    already_seen: bool = False,
) -> ScanOutcome:
    """Apply a take or restock code to an item's batches.

    Set `already_seen` when this station + scanned_at was processed before.
    """
    if already_seen:
        return ScanOutcome(OutcomeStatus.DUPLICATE)
    if code.item_id != item.item_id:
        raise ValueError(f"Code {code.code} belongs to {code.item_id}, not {item.item_id}")
    batches = _batches_for(item.item_id, batches)

    if code.action is CodeAction.TAKE:
        return _take(request, code, item, batches)
    return _restock(request, code, item, batches)


def build_undo(
    request: ScanRequest,
    last_entry: LogEntry | None,
    batches: Iterable[Batch],
    *,
    already_seen: bool = False,
) -> ScanOutcome:
    """Reverse the station's most recent log entry by adding an opposite entry.

    `last_entry` is the newest entry from this station (or None) and `batches` are the
    current batches of its item. If the newest entry is already an undo, there is
    nothing to reverse, so pressing INV-UNDO twice does not reach older scans.
    """
    if already_seen:
        return ScanOutcome(OutcomeStatus.DUPLICATE)
    if last_entry is None or last_entry.action not in (LogAction.TAKE, LogAction.RESTOCK):
        return ScanOutcome(OutcomeStatus.NOTHING_TO_UNDO)
    if last_entry.station != request.station:
        raise ValueError("A station can only undo its own scans")

    batches = _batches_for(last_entry.item_id, batches)
    reversal = [Allocation(a.batch_id, -a.change) for a in last_entry.allocations]
    changes = apply_allocations(batches, reversal)
    entry = _new_entry(
        request,
        item_id=last_entry.item_id,
        action=LogAction.UNDO,
        code=UNDO_CODE,
        allocations=changes.allocations,
        shortfall=changes.shortfall,
        undoes=last_entry.entry_id,
    )
    return ScanOutcome(OutcomeStatus.APPLIED, entry=entry, batches=changes.batches)


def _take(request: ScanRequest, code: Code, item: Item, batches: list[Batch]) -> ScanOutcome:
    deduction = deduct_fifo(batches, -code.quantity)
    changes = apply_allocations(batches, deduction.allocations)
    entry = _new_entry(
        request,
        item_id=item.item_id,
        action=LogAction.TAKE,
        code=code.code,
        allocations=changes.allocations,
        # FIFO never plans more than a batch holds, so the shortfall is the deduction's.
        shortfall=deduction.shortfall,
    )
    return ScanOutcome(OutcomeStatus.APPLIED, entry=entry, batches=changes.batches)


def _restock(request: ScanRequest, code: Code, item: Item, batches: list[Batch]) -> ScanOutcome:
    if item.has_expiry:
        return ScanOutcome(
            OutcomeStatus.REJECTED,
            reason="Items with an expiry date are restocked with POST /batches "
            "so the expiry date is recorded.",
        )

    if not any(b.batch_id == DEFAULT_BATCH_ID for b in batches):
        first_batch = Batch(
            item_id=item.item_id,
            batch_id=DEFAULT_BATCH_ID,
            quantity=0,
            received_on=request.scanned_at.date(),
        )
        batches = [*batches, first_batch]

    changes = apply_allocations(batches, [Allocation(DEFAULT_BATCH_ID, code.quantity)])
    entry = _new_entry(
        request,
        item_id=item.item_id,
        action=LogAction.RESTOCK,
        code=code.code,
        allocations=changes.allocations,
        shortfall=0,
    )
    return ScanOutcome(OutcomeStatus.APPLIED, entry=entry, batches=changes.batches)


def _batches_for(item_id: str, batches: Iterable[Batch]) -> list[Batch]:
    batches = list(batches)
    for batch in batches:
        if batch.item_id != item_id:
            raise ValueError(f"Batch {batch.batch_id} belongs to {batch.item_id}, not {item_id}")
    return batches


def _new_entry(
    request: ScanRequest,
    *,
    item_id: str,
    action: LogAction,
    code: str,
    allocations: tuple[Allocation, ...],
    shortfall: int,
    undoes: str | None = None,
) -> LogEntry:
    return LogEntry(
        entry_id=scan_id(request.station, request.scanned_at),
        item_id=item_id,
        timestamp=request.scanned_at.astimezone(UTC),
        action=action,
        code=code,
        staff=request.staff,
        station=request.station,
        allocations=allocations,
        shortfall=shortfall,
        undoes=undoes,
    )

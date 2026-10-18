"""Small builders for test data, so each test only spells out what it cares about."""

from datetime import UTC, date, datetime, timedelta

from core.codes import UNDO_CODE
from core.models import Batch, Code, CodeAction, Item, ScanAction, ScanOutcome, ScanRequest

T0 = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
TODAY = T0.date()

LIDS = Item(item_id="lid12", name="12 oz lids", smallest_unit="bag", has_expiry=False)
MILK = Item(item_id="milk", name="Whole milk", smallest_unit="carton", has_expiry=True)

LID_BAG = Code("INV-LID12-1BAG", "lid12", -1, CodeAction.TAKE)
LID_BOX = Code("INV-LID12-1BOX", "lid12", -12, CodeAction.TAKE)  # 1 box = 12 bags
LID_RESTOCK_BOX = Code("INV-LID12-RESTOCK-BOX", "lid12", 12, CodeAction.RESTOCK)
MILK_CRATE = Code("INV-MILK-1CRATE", "milk", -6, CodeAction.TAKE)  # 1 crate = 6 cartons
MILK_RESTOCK_CRATE = Code("INV-MILK-RESTOCK-CRATE", "milk", 6, CodeAction.RESTOCK)


def batch(
    item_id: str,
    batch_id: str,
    quantity: int,
    *,
    expires_in_days: int | None = None,
    received_days_ago: int = 0,
) -> Batch:
    expires_on: date | None = None
    if expires_in_days is not None:
        expires_on = TODAY + timedelta(days=expires_in_days)
    return Batch(
        item_id=item_id,
        batch_id=batch_id,
        quantity=quantity,
        received_on=TODAY - timedelta(days=received_days_ago),
        expires_on=expires_on,
    )


def scan(
    code: str,
    *,
    seconds: int = 0,
    station: str = "door-1",
    staff: str = "STAFF-001",
    action: ScanAction = ScanAction.SCAN,
) -> ScanRequest:
    """A scan made `seconds` after T0."""
    return ScanRequest(
        code=code,
        action=action,
        staff=staff,
        station=station,
        scanned_at=T0 + timedelta(seconds=seconds),
    )


def undo(*, seconds: int, station: str = "door-1") -> ScanRequest:
    return scan(UNDO_CODE, seconds=seconds, station=station, action=ScanAction.UNDO)


def saved(batches: list[Batch], outcome: ScanOutcome) -> list[Batch]:
    """The batches as the table would hold them after the handler saves `outcome`."""
    by_id = {b.batch_id: b for b in batches}
    by_id.update({b.batch_id: b for b in outcome.batches})
    return list(by_id.values())


def quantities(batches: tuple[Batch, ...] | list[Batch]) -> dict[str, int]:
    return {b.batch_id: b.quantity for b in batches}

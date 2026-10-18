"""Small builders for test data, so each test only spells out what it cares about."""

from datetime import UTC, datetime, timedelta

from core.codes import UNDO_CODE
from core.models import Code, CodeAction, Item, ScanAction, ScanRequest

T0 = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)

LID_BAG = Code("INV-LID12-1BAG", "lid12", -1, CodeAction.TAKE)
LID_BOX = Code("INV-LID12-1BOX", "lid12", -12, CodeAction.TAKE)  # 1 box = 12 bags
LID_RESTOCK_BOX = Code("INV-LID12-RESTOCK-BOX", "lid12", 12, CodeAction.RESTOCK)


def lids(stock: int = 30, *, needs_count: bool = False) -> Item:
    """12 oz lids, counted in bags."""
    return Item(
        item_id="lid12",
        name="12 oz lids",
        smallest_unit="bag",
        stock=stock,
        needs_count=needs_count,
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

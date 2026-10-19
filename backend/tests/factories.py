"""Small builders for test data, so each test only spells out what it cares about."""

from datetime import UTC, datetime, timedelta

from core.models import Code, CodeAction, Item, ScanAction, ScanRequest, ShopSettings, Staff

# 17:00 UTC is 10:00 in Los Angeles: a normal morning in the test shop.
T0 = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)
SHOP_TZ = "America/Los_Angeles"

NO_BADGES = ShopSettings(timezone=SHOP_TZ)
BADGES = ShopSettings(timezone=SHOP_TZ, require_staff_badge=True)
BADGES_OWN_UNDO = ShopSettings(timezone=SHOP_TZ, require_staff_badge=True, undo_own_scans_only=True)

ANNA = Staff(staff_code="STAFF-001", name="Anna")
BEN = Staff(staff_code="STAFF-002", name="Ben")

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
    staff: str | None = None,
    action: ScanAction = ScanAction.SCAN,
) -> ScanRequest:
    """A scan made `seconds` after T0 (negative = before T0)."""
    return ScanRequest(
        code=code,
        action=action,
        staff=staff,
        station=station,
        scanned_at=T0 + timedelta(seconds=seconds),
    )


def undo(
    code: str, *, seconds: int, station: str = "door-1", staff: str | None = None
) -> ScanRequest:
    """UNDO followed by `code`, as the station sends it."""
    return scan(code, seconds=seconds, station=station, staff=staff, action=ScanAction.UNDO)

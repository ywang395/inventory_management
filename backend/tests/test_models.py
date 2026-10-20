from datetime import datetime

import pytest

from core.models import Code, CodeAction, ScanAction, ScanRequest
from factories import T0


@pytest.mark.parametrize(
    "action, quantity",
    [
        (CodeAction.TAKE, 1),
        (CodeAction.TAKE, 0),
        (CodeAction.RESTOCK, -1),
        (CodeAction.RESTOCK, 0),
    ],
)
def test_code_quantity_sign_must_match_action(action: CodeAction, quantity: int) -> None:
    with pytest.raises(ValueError):
        Code("INV-TEST", "test", quantity, action)


def test_scan_request_needs_time_zone() -> None:
    with pytest.raises(ValueError, match="time zone"):
        ScanRequest(
            "INV-LID12-1BAG", ScanAction.SCAN, "STAFF-001", "door-1", datetime(2026, 10, 17)
        )


def test_scan_request_needs_station() -> None:
    with pytest.raises(ValueError, match="station"):
        ScanRequest("INV-LID12-1BAG", ScanAction.SCAN, "STAFF-001", "", T0)


def test_codes_are_saved_the_way_scans_arrive() -> None:
    assert Code(" inv-test\n", "test", -1, CodeAction.TAKE).code == "INV-TEST"
    assert Code("0647658068411", "candle", -1, CodeAction.TAKE).code == "647658068411"

from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.models import Item, LogAction, OutcomeStatus, Staff
from core.scan import clamp_stock, format_utc, process_scan, scan_id
from factories import (
    ANNA,
    BADGES,
    LID_BAG,
    LID_BOX,
    LID_RESTOCK_BOX,
    NO_BADGES,
    T0,
    lids,
    scan,
)

# --- Units: a code can stand for several smallest units -------------------------------


def test_box_code_deducts_twelve_bags() -> None:
    outcome = process_scan(scan(LID_BOX.code), LID_BOX, lids(stock=30), settings=NO_BADGES)
    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 18
    assert outcome.entry.action is LogAction.TAKE
    assert outcome.entry.change == -12
    assert not outcome.item.needs_count


def test_bag_code_deducts_one_bag() -> None:
    outcome = process_scan(scan(LID_BAG.code), LID_BAG, lids(stock=30), settings=NO_BADGES)
    assert outcome.item is not None
    assert outcome.item.stock == 29


def test_restock_code_adds_twelve_bags() -> None:
    outcome = process_scan(
        scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, lids(stock=3), settings=NO_BADGES
    )
    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 15
    assert outcome.entry.action is LogAction.RESTOCK
    assert outcome.entry.change == 12


# --- Never go negative ----------------------------------------------------------------


def test_taking_more_than_stock_clamps_at_zero_and_flags_a_count() -> None:
    outcome = process_scan(scan(LID_BOX.code), LID_BOX, lids(stock=5), settings=NO_BADGES)
    assert outcome.status is OutcomeStatus.APPLIED  # the scan is still recorded
    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 0
    assert outcome.item.needs_count
    assert outcome.entry.change == -5
    assert outcome.entry.shortfall == 7


def test_taking_with_no_stock_records_the_full_shortfall() -> None:
    outcome = process_scan(scan(LID_BAG.code), LID_BAG, lids(stock=0), settings=NO_BADGES)
    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 0
    assert outcome.item.needs_count
    assert outcome.entry.change == 0
    assert outcome.entry.shortfall == 1


def test_restock_does_not_clear_the_count_flag() -> None:
    # Only a manual count (POST /corrections) should clear it.
    item = lids(stock=0, needs_count=True)
    outcome = process_scan(scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, item, settings=NO_BADGES)
    assert outcome.item is not None
    assert outcome.item.needs_count


@pytest.mark.parametrize(
    "stock, change, expected",
    [
        (10, -3, (7, 0)),
        (10, 5, (15, 0)),
        (2, -5, (0, 3)),
        (0, -1, (0, 1)),
    ],
)
def test_clamp_stock(stock: int, change: int, expected: tuple[int, int]) -> None:
    assert clamp_stock(stock, change) == expected


# --- Staff badges (owner's choice) ----------------------------------------------------


def test_without_badges_a_scan_needs_no_staff() -> None:
    outcome = process_scan(scan(LID_BAG.code, staff=None), LID_BAG, lids(), settings=NO_BADGES)
    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.entry is not None
    assert outcome.entry.staff is None


def test_with_badges_a_scan_without_a_badge_is_rejected() -> None:
    outcome = process_scan(scan(LID_BAG.code, staff=None), LID_BAG, lids(), settings=BADGES)
    assert outcome.status is OutcomeStatus.REJECTED
    assert outcome.reason == "Scan your staff badge first."
    assert outcome.entry is None
    assert outcome.item is None


def test_with_badges_an_unknown_badge_is_rejected() -> None:
    request = scan(LID_BAG.code, staff="STAFF-999")
    outcome = process_scan(request, LID_BAG, lids(), settings=BADGES, staff=None)
    assert outcome.status is OutcomeStatus.REJECTED
    assert "Unknown staff badge STAFF-999" in outcome.reason


def test_with_badges_an_inactive_badge_is_rejected() -> None:
    former = Staff(staff_code="STAFF-003", name="Cleo", active=False)
    request = scan(LID_BAG.code, staff="STAFF-003")
    outcome = process_scan(request, LID_BAG, lids(), settings=BADGES, staff=former)
    assert outcome.status is OutcomeStatus.REJECTED
    assert "no longer active" in outcome.reason


def test_with_badges_a_valid_badge_is_recorded() -> None:
    request = scan(LID_BAG.code, staff=ANNA.staff_code)
    outcome = process_scan(request, LID_BAG, lids(), settings=BADGES, staff=ANNA)
    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.entry is not None
    assert outcome.entry.staff == "STAFF-001"


# --- Log entry contents ---------------------------------------------------------------


def test_log_entry_records_where_and_when() -> None:
    request = scan(LID_BAG.code, station="door-2", staff="STAFF-042")
    entry = process_scan(request, LID_BAG, lids(), settings=NO_BADGES).entry
    assert entry is not None
    assert entry.entry_id == scan_id("door-2", request.scanned_at)
    assert entry.item_id == "lid12"
    assert entry.code == "INV-LID12-1BAG"
    assert entry.staff == "STAFF-042"  # recorded even when badges aren't required
    assert entry.station == "door-2"
    assert entry.timestamp == request.scanned_at
    assert entry.undoes is None


# --- Duplicates (offline queue may resend) --------------------------------------------


def test_same_moment_gets_same_id_in_any_time_zone() -> None:
    utc = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    plus_two = datetime(2026, 10, 17, 10, 0, tzinfo=timezone(timedelta(hours=2)))
    assert scan_id("door-1", utc) == scan_id("door-1", plus_two)


def test_scan_id_differs_by_station_and_time() -> None:
    assert scan_id("door-1", T0) != scan_id("door-2", T0)
    assert scan_id("door-1", T0) != scan_id("door-1", T0 + timedelta(microseconds=1))


def test_scan_id_needs_time_zone() -> None:
    with pytest.raises(ValueError):
        scan_id("door-1", datetime(2026, 10, 17, 8, 0))


def test_format_utc_is_fixed_width_and_sortable() -> None:
    assert format_utc(T0) == "2026-10-17T17:00:00.000000+00:00"
    assert format_utc(T0 + timedelta(microseconds=5)) == "2026-10-17T17:00:00.000005+00:00"


def test_resent_scan_gets_the_same_entry_id() -> None:
    first = process_scan(scan(LID_BAG.code), LID_BAG, lids(), settings=NO_BADGES)
    resent = process_scan(scan(LID_BAG.code), LID_BAG, lids(), settings=NO_BADGES)
    assert first.entry is not None and resent.entry is not None
    assert first.entry.entry_id == resent.entry.entry_id


def test_already_seen_scan_is_a_duplicate_and_changes_nothing() -> None:
    outcome = process_scan(
        scan(LID_BOX.code), LID_BOX, lids(), settings=NO_BADGES, already_seen=True
    )
    assert outcome.status is OutcomeStatus.DUPLICATE
    assert outcome.entry is None
    assert outcome.item is None


# --- Safety checks --------------------------------------------------------------------


def test_code_for_another_item_is_an_error() -> None:
    milk = Item(item_id="milk", name="Whole milk", smallest_unit="carton")
    with pytest.raises(ValueError):
        process_scan(scan(LID_BAG.code), LID_BAG, milk, settings=NO_BADGES)

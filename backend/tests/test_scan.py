from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.models import Allocation, LogAction, OutcomeStatus
from core.scan import DEFAULT_BATCH_ID, process_scan, scan_id
from factories import (
    LID_BAG,
    LID_BOX,
    LID_RESTOCK_BOX,
    LIDS,
    MILK,
    MILK_CRATE,
    MILK_RESTOCK_CRATE,
    T0,
    batch,
    quantities,
    scan,
)

# --- Units: a code can stand for several smallest units -------------------------------


def test_box_code_deducts_twelve_bags() -> None:
    outcome = process_scan(scan(LID_BOX.code), LID_BOX, LIDS, [batch("lid12", "default", 30)])
    assert outcome.status is OutcomeStatus.APPLIED
    assert quantities(outcome.batches) == {"default": 18}
    assert outcome.entry is not None
    assert outcome.entry.action is LogAction.TAKE
    assert outcome.entry.change == -12
    assert not outcome.needs_count


def test_bag_code_deducts_one_bag() -> None:
    outcome = process_scan(scan(LID_BAG.code), LID_BAG, LIDS, [batch("lid12", "default", 30)])
    assert quantities(outcome.batches) == {"default": 29}


def test_restock_code_adds_to_the_single_batch() -> None:
    outcome = process_scan(
        scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, LIDS, [batch("lid12", DEFAULT_BATCH_ID, 3)]
    )
    assert quantities(outcome.batches) == {DEFAULT_BATCH_ID: 15}
    assert outcome.entry is not None
    assert outcome.entry.action is LogAction.RESTOCK
    assert outcome.entry.change == 12


def test_first_restock_creates_the_single_batch() -> None:
    outcome = process_scan(scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, LIDS, [])
    (new_batch,) = outcome.batches
    assert new_batch.batch_id == DEFAULT_BATCH_ID
    assert new_batch.quantity == 12
    assert new_batch.received_on == T0.date()


def test_restock_scan_is_rejected_for_items_with_expiry() -> None:
    outcome = process_scan(scan(MILK_RESTOCK_CRATE.code), MILK_RESTOCK_CRATE, MILK, [])
    assert outcome.status is OutcomeStatus.REJECTED
    assert "POST /batches" in outcome.reason
    assert outcome.entry is None
    assert outcome.batches == ()


# --- FIFO through a real scan ---------------------------------------------------------


def test_take_uses_oldest_batch_first_and_can_span_batches() -> None:
    batches = [
        batch("milk", "b-new", 10, expires_in_days=7),
        batch("milk", "b-old", 4, expires_in_days=2),
    ]
    outcome = process_scan(scan(MILK_CRATE.code), MILK_CRATE, MILK, batches)
    assert outcome.entry is not None
    assert outcome.entry.allocations == (Allocation("b-old", -4), Allocation("b-new", -2))
    assert quantities(outcome.batches) == {"b-old": 0, "b-new": 8}


# --- Never go negative ----------------------------------------------------------------


def test_taking_more_than_stock_clamps_at_zero_and_flags_a_count() -> None:
    outcome = process_scan(scan(LID_BOX.code), LID_BOX, LIDS, [batch("lid12", "default", 5)])
    assert outcome.status is OutcomeStatus.APPLIED  # the scan is still recorded
    assert quantities(outcome.batches) == {"default": 0}
    assert outcome.entry is not None
    assert outcome.entry.change == -5
    assert outcome.entry.shortfall == 7
    assert outcome.needs_count


def test_taking_with_no_stock_records_the_full_shortfall() -> None:
    outcome = process_scan(scan(LID_BAG.code), LID_BAG, LIDS, [])
    assert outcome.entry is not None
    assert outcome.entry.allocations == ()
    assert outcome.entry.shortfall == 1
    assert outcome.batches == ()
    assert outcome.needs_count


# --- Log entry contents ---------------------------------------------------------------


def test_log_entry_records_who_where_and_when() -> None:
    request = scan(LID_BAG.code, station="door-2", staff="STAFF-042")
    outcome = process_scan(request, LID_BAG, LIDS, [batch("lid12", "default", 30)])
    entry = outcome.entry
    assert entry is not None
    assert entry.entry_id == scan_id("door-2", request.scanned_at)
    assert entry.item_id == "lid12"
    assert entry.code == "INV-LID12-1BAG"
    assert entry.staff == "STAFF-042"
    assert entry.station == "door-2"
    assert entry.timestamp == request.scanned_at
    assert entry.undoes is None


def test_input_batches_are_not_changed() -> None:
    batches = [batch("lid12", "default", 30)]
    process_scan(scan(LID_BOX.code), LID_BOX, LIDS, batches)
    assert quantities(batches) == {"default": 30}


# --- Duplicates (offline queue may resend) --------------------------------------------


def test_same_moment_gets_same_id_in_any_time_zone() -> None:
    utc = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    plus_two = datetime(2026, 10, 17, 10, 0, tzinfo=timezone(timedelta(hours=2)))
    assert scan_id("door-1", utc) == scan_id("door-1", plus_two)


def test_scan_id_differs_by_station_and_time() -> None:
    assert scan_id("door-1", T0) != scan_id("door-2", T0)
    assert scan_id("door-1", T0) != scan_id("door-1", T0 + timedelta(milliseconds=1))


def test_scan_id_needs_time_zone() -> None:
    with pytest.raises(ValueError):
        scan_id("door-1", datetime(2026, 10, 17, 8, 0))


def test_resent_scan_gets_the_same_entry_id() -> None:
    batches = [batch("lid12", "default", 30)]
    first = process_scan(scan(LID_BAG.code), LID_BAG, LIDS, batches)
    resent = process_scan(scan(LID_BAG.code), LID_BAG, LIDS, batches)
    assert first.entry is not None and resent.entry is not None
    assert first.entry.entry_id == resent.entry.entry_id


def test_already_seen_scan_is_a_duplicate_and_changes_nothing() -> None:
    outcome = process_scan(
        scan(LID_BOX.code), LID_BOX, LIDS, [batch("lid12", "default", 30)], already_seen=True
    )
    assert outcome.status is OutcomeStatus.DUPLICATE
    assert outcome.entry is None
    assert outcome.batches == ()


# --- Safety checks --------------------------------------------------------------------


def test_code_for_another_item_is_an_error() -> None:
    with pytest.raises(ValueError):
        process_scan(scan(MILK_CRATE.code), MILK_CRATE, LIDS, [])


def test_batch_for_another_item_is_an_error() -> None:
    with pytest.raises(ValueError):
        process_scan(scan(LID_BAG.code), LID_BAG, LIDS, [batch("milk", "b1", 3)])

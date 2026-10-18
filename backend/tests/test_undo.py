import pytest

from core.models import Allocation, LogAction, OutcomeStatus
from core.scan import DEFAULT_BATCH_ID, build_undo, process_scan, scan_id
from factories import (
    LID_BAG,
    LID_BOX,
    LID_RESTOCK_BOX,
    LIDS,
    MILK,
    MILK_CRATE,
    batch,
    quantities,
    saved,
    scan,
    undo,
)


def milk_batches():
    return [
        batch("milk", "b-old", 4, expires_in_days=2),
        batch("milk", "b-new", 10, expires_in_days=7),
    ]


def test_undo_puts_back_exactly_what_was_taken() -> None:
    before = milk_batches()
    taken = process_scan(scan(MILK_CRATE.code), MILK_CRATE, MILK, before)

    outcome = build_undo(undo(seconds=5), taken.entry, saved(before, taken))

    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.entry is not None
    assert outcome.entry.allocations == (Allocation("b-old", 4), Allocation("b-new", 2))
    assert quantities(outcome.batches) == quantities(before)


def test_undo_entry_points_at_the_entry_it_reverses() -> None:
    before = milk_batches()
    taken = process_scan(scan(MILK_CRATE.code), MILK_CRATE, MILK, before)
    request = undo(seconds=5)

    outcome = build_undo(request, taken.entry, saved(before, taken))

    entry = outcome.entry
    assert entry is not None and taken.entry is not None
    assert entry.action is LogAction.UNDO
    assert entry.code == "INV-UNDO"
    assert entry.undoes == taken.entry.entry_id
    assert entry.entry_id == scan_id("door-1", request.scanned_at)
    assert entry.item_id == "milk"


def test_undo_after_a_clamped_take_restores_only_what_was_taken() -> None:
    before = [batch("lid12", "default", 5)]
    taken = process_scan(scan(LID_BOX.code), LID_BOX, LIDS, before)  # asked 12, had 5

    outcome = build_undo(undo(seconds=5), taken.entry, saved(before, taken))

    assert quantities(outcome.batches) == {"default": 5}
    assert outcome.entry is not None
    assert outcome.entry.change == 5
    assert not outcome.needs_count


def test_undo_of_a_take_that_found_no_stock_changes_nothing() -> None:
    taken = process_scan(scan(LID_BAG.code), LID_BAG, LIDS, [])

    outcome = build_undo(undo(seconds=5), taken.entry, [])

    # Still recorded, so a second INV-UNDO knows the last scan was already undone.
    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.entry is not None
    assert outcome.entry.allocations == ()
    assert outcome.batches == ()


def test_undo_of_a_restock_removes_it() -> None:
    before = [batch("lid12", DEFAULT_BATCH_ID, 3)]
    restocked = process_scan(scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, LIDS, before)

    outcome = build_undo(undo(seconds=5), restocked.entry, saved(before, restocked))

    assert quantities(outcome.batches) == {DEFAULT_BATCH_ID: 3}


def test_undo_of_a_restock_already_partly_used_clamps_at_zero_and_flags_a_count() -> None:
    restocked = process_scan(scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, LIDS, [])
    # 8 of the 12 bags were used (e.g. by another station) before the undo arrived.
    now = [batch("lid12", DEFAULT_BATCH_ID, 4)]

    outcome = build_undo(undo(seconds=60), restocked.entry, now)

    assert quantities(outcome.batches) == {DEFAULT_BATCH_ID: 0}
    assert outcome.entry is not None
    assert outcome.entry.change == -4
    assert outcome.entry.shortfall == 8
    assert outcome.needs_count


def test_second_undo_does_nothing() -> None:
    before = milk_batches()
    taken = process_scan(scan(MILK_CRATE.code), MILK_CRATE, MILK, before)
    after_take = saved(before, taken)
    first_undo = build_undo(undo(seconds=5), taken.entry, after_take)

    second_undo = build_undo(undo(seconds=10), first_undo.entry, saved(after_take, first_undo))

    assert second_undo.status is OutcomeStatus.NOTHING_TO_UNDO
    assert second_undo.entry is None
    assert second_undo.batches == ()


def test_undo_with_no_earlier_scan_does_nothing() -> None:
    outcome = build_undo(undo(seconds=0), None, [])
    assert outcome.status is OutcomeStatus.NOTHING_TO_UNDO


def test_station_cannot_undo_another_stations_scan() -> None:
    before = [batch("lid12", "default", 5)]
    taken = process_scan(scan(LID_BAG.code, station="door-1"), LID_BAG, LIDS, before)

    with pytest.raises(ValueError):
        build_undo(undo(seconds=5, station="door-2"), taken.entry, saved(before, taken))


def test_resent_undo_is_a_duplicate() -> None:
    before = [batch("lid12", "default", 5)]
    taken = process_scan(scan(LID_BAG.code), LID_BAG, LIDS, before)

    outcome = build_undo(undo(seconds=5), taken.entry, saved(before, taken), already_seen=True)

    assert outcome.status is OutcomeStatus.DUPLICATE
    assert outcome.batches == ()

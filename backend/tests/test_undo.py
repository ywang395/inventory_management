import pytest

from core.models import Item, LogAction, OutcomeStatus
from core.scan import build_undo, process_scan, scan_id
from factories import LID_BAG, LID_BOX, LID_RESTOCK_BOX, lids, scan, undo


def test_undo_puts_back_what_was_taken() -> None:
    taken = process_scan(scan(LID_BOX.code), LID_BOX, lids(stock=30))

    outcome = build_undo(undo(seconds=5), taken.entry, taken.item)

    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 30
    assert outcome.entry.change == 12


def test_undo_entry_points_at_the_entry_it_reverses() -> None:
    taken = process_scan(scan(LID_BOX.code), LID_BOX, lids())
    request = undo(seconds=5)

    entry = build_undo(request, taken.entry, taken.item).entry

    assert entry is not None and taken.entry is not None
    assert entry.action is LogAction.UNDO
    assert entry.code == "INV-UNDO"
    assert entry.undoes == taken.entry.entry_id
    assert entry.entry_id == scan_id("door-1", request.scanned_at)
    assert entry.item_id == "lid12"


def test_undo_after_a_clamped_take_restores_only_what_was_taken() -> None:
    taken = process_scan(scan(LID_BOX.code), LID_BOX, lids(stock=5))  # asked 12, had 5

    outcome = build_undo(undo(seconds=5), taken.entry, taken.item)

    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 5
    assert outcome.entry.change == 5


def test_undo_of_a_take_that_found_no_stock_changes_nothing() -> None:
    taken = process_scan(scan(LID_BAG.code), LID_BAG, lids(stock=0))

    outcome = build_undo(undo(seconds=5), taken.entry, taken.item)

    # Still recorded, so a second INV-UNDO knows the last scan was already undone.
    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 0
    assert outcome.entry.change == 0


def test_undo_of_a_restock_removes_it() -> None:
    restocked = process_scan(scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, lids(stock=3))

    outcome = build_undo(undo(seconds=5), restocked.entry, restocked.item)

    assert outcome.item is not None
    assert outcome.item.stock == 3


def test_undo_of_a_restock_already_partly_used_clamps_at_zero_and_flags_a_count() -> None:
    restocked = process_scan(scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, lids(stock=0))
    # 8 of the 12 bags were used (e.g. at another station) before the undo arrived.
    now = lids(stock=4)

    outcome = build_undo(undo(seconds=60), restocked.entry, now)

    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 0
    assert outcome.item.needs_count
    assert outcome.entry.change == -4
    assert outcome.entry.shortfall == 8


def test_second_undo_does_nothing() -> None:
    taken = process_scan(scan(LID_BOX.code), LID_BOX, lids())
    first_undo = build_undo(undo(seconds=5), taken.entry, taken.item)

    second_undo = build_undo(undo(seconds=10), first_undo.entry, first_undo.item)

    assert second_undo.status is OutcomeStatus.NOTHING_TO_UNDO
    assert second_undo.entry is None
    assert second_undo.item is None


def test_undo_with_no_earlier_scan_does_nothing() -> None:
    outcome = build_undo(undo(seconds=0), None, None)
    assert outcome.status is OutcomeStatus.NOTHING_TO_UNDO


def test_station_cannot_undo_another_stations_scan() -> None:
    taken = process_scan(scan(LID_BAG.code, station="door-1"), LID_BAG, lids())

    with pytest.raises(ValueError):
        build_undo(undo(seconds=5, station="door-2"), taken.entry, taken.item)


def test_undo_with_the_wrong_item_is_an_error() -> None:
    taken = process_scan(scan(LID_BAG.code), LID_BAG, lids())
    milk = Item(item_id="milk", name="Whole milk", smallest_unit="carton")

    with pytest.raises(ValueError):
        build_undo(undo(seconds=5), taken.entry, milk)


def test_resent_undo_is_a_duplicate() -> None:
    taken = process_scan(scan(LID_BAG.code), LID_BAG, lids())

    outcome = build_undo(undo(seconds=5), taken.entry, taken.item, already_seen=True)

    assert outcome.status is OutcomeStatus.DUPLICATE
    assert outcome.item is None

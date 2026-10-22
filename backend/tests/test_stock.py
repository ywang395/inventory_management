"""Rebuilding stock from the log gives the same number as the running total."""

from dataclasses import replace

import pytest

from core.models import Code, Item, LogEntry, ScanAction
from core.scan import build_undo, find_undo_target, process_scan
from core.stock import StockStatus, recalculate_stock, stock_status
from factories import LID_BAG, LID_BOX, LID_RESTOCK_BOX, NO_BADGES, lids, scan, undo

SCAN, UNDO = ScanAction.SCAN, ScanAction.UNDO


def replay(*steps: tuple[ScanAction, Code]) -> tuple[Item, list[LogEntry]]:
    """Run scans and undos through core one after another, starting from 0 bags.

    Returns the item (with its running stock) and every log entry written.
    """
    item = lids(stock=0)
    entries: list[LogEntry] = []
    for seconds, (action, code) in enumerate(steps):
        if action is SCAN:
            request = scan(code.code, seconds=seconds)
            outcome = process_scan(request, code, item, settings=NO_BADGES)
        else:
            request = undo(code.code, seconds=seconds)
            target = find_undo_target(request, entries, NO_BADGES)
            outcome = build_undo(request, target, item, settings=NO_BADGES)
        assert outcome.entry is not None and outcome.item is not None
        item = outcome.item
        entries.append(outcome.entry)
    return item, entries


def test_no_entries_means_no_stock() -> None:
    assert recalculate_stock([]) == 0


def test_stock_is_the_sum_of_restocks_and_takes() -> None:
    item, entries = replay((SCAN, LID_RESTOCK_BOX), (SCAN, LID_BAG), (SCAN, LID_BAG))

    assert item.stock == 10
    assert recalculate_stock(entries) == 10


def test_an_undo_cancels_its_scan() -> None:
    item, entries = replay((SCAN, LID_RESTOCK_BOX), (SCAN, LID_BAG), (UNDO, LID_BAG))

    assert item.stock == 12
    assert recalculate_stock(entries) == 12


def test_a_clamped_take_counts_only_what_was_taken() -> None:
    # 12 bags in, 1 out, then a box (12) is scanned but only 11 bags are left.
    item, entries = replay((SCAN, LID_RESTOCK_BOX), (SCAN, LID_BAG), (SCAN, LID_BOX))

    assert entries[-1].change == -11
    assert item.stock == 0
    assert recalculate_stock(entries) == 0


def test_undoing_a_clamped_take_gives_back_only_what_was_taken() -> None:
    item, entries = replay(
        (SCAN, LID_RESTOCK_BOX), (SCAN, LID_BAG), (SCAN, LID_BOX), (UNDO, LID_BOX)
    )

    assert item.stock == 11
    assert recalculate_stock(entries) == 11


def test_order_does_not_matter() -> None:
    # An offline station's scans can reach the API long after they were made.
    _, entries = replay((SCAN, LID_RESTOCK_BOX), (SCAN, LID_BAG), (SCAN, LID_BOX))

    assert recalculate_stock(reversed(entries)) == recalculate_stock(entries)


def test_entries_must_all_be_for_one_item() -> None:
    _, entries = replay((SCAN, LID_RESTOCK_BOX))
    other_item = replace(entries[0], item_id="cup12")

    with pytest.raises(ValueError, match="several items"):
        recalculate_stock([entries[0], other_item])


# --- Status for the stock board / dashboard ---------------------------------------------


@pytest.mark.parametrize(
    "stock, needs_count, status",
    [
        (11, False, StockStatus.OK),  # above the reorder point (10)
        (10, False, StockStatus.LOW),  # at the reorder point: time to order
        (3, False, StockStatus.LOW),
        (0, False, StockStatus.OUT),
        (0, True, StockStatus.NEEDS_COUNT),  # a scan took more than we had
        (25, True, StockStatus.NEEDS_COUNT),  # restocked since, but still worth a count
    ],
)
def test_stock_status(stock: int, needs_count: bool, status: StockStatus) -> None:
    item = replace(lids(stock=stock, needs_count=needs_count), reorder_point=10)
    assert stock_status(item) is status

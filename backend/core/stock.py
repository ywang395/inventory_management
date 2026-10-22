"""An item's stock: rebuilt from its log entries, and how healthy it looks.

The log is the source of truth: every scan, restock, undo and correction records the
`change` it actually applied to stock, so an item's stock is the sum of those changes.
Corrections and audits use this to check or repair the running `stock` number.
"""

from collections.abc import Iterable
from enum import StrEnum

from core.models import Item, LogEntry


class StockStatus(StrEnum):
    OK = "ok"
    LOW = "low"  # at or below the reorder point: time to order
    OUT = "out"  # nothing left
    NEEDS_COUNT = "needs_count"  # a scan took more than we thought was there


def stock_status(item: Item) -> StockStatus:
    """One status per item, for the stock board and later the dashboard's colors.

    NEEDS_COUNT comes first: if the number may be wrong, low/out isn't reliable either.
    """
    if item.needs_count:
        return StockStatus.NEEDS_COUNT
    if item.stock == 0:
        return StockStatus.OUT
    if item.stock <= item.reorder_point:
        return StockStatus.LOW
    return StockStatus.OK


def recalculate_stock(entries: Iterable[LogEntry]) -> int:
    """Stock of one item = the sum of `change` over all of its log entries.

    The order of the entries doesn't matter. That's important: an offline station may
    send this morning's scans in the afternoon, so `scanned_at` order isn't the order
    the changes were applied in. Only the total counts.

    Opening stock counts only if it was logged too (e.g. as a correction entry). A result
    that differs from the item's `stock`, or is negative, means entries are missing.
    """
    entries = list(entries)
    item_ids = {e.item_id for e in entries}
    if len(item_ids) > 1:
        raise ValueError(f"Entries are for several items: {sorted(item_ids)}")
    return sum(e.change for e in entries)

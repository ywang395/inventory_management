"""When a stock change deserves an email to the owner.

The alerts Lambda gets every item update with the item before and after. An email goes
out only when things get worse, so one drop means one email, not one per scan:

- COUNT: a scan took more than was in stock (needs_count switched on)
- OUT:   stock reached 0
- LOW:   stock dropped to or below the reorder point

Restocks, undos that put stock back, and changes to other fields send nothing.
"""

from dataclasses import dataclass
from enum import StrEnum

from core.models import Item
from core.stock import units


class AlertKind(StrEnum):
    LOW = "low"
    OUT = "out"
    COUNT = "count"


@dataclass(frozen=True)
class StockAlert:
    kind: AlertKind
    subject: str  # short, for the email subject line
    text: str  # the email body


def stock_alert(before: Item | None, after: Item) -> StockAlert | None:
    """The alert this change deserves, or None. `before` is None for a new item."""
    if before is None:
        return None  # a new item hasn't dropped from anywhere
    left = units(after.stock, after.smallest_unit)

    if after.needs_count and not before.needs_count:
        return StockAlert(
            AlertKind.COUNT,
            f"Please count: {after.name}",
            f"A scan took more {after.name} than the system had in stock. "
            f"Stock is now {left}: please count it.",
        )
    if _level(after) <= _level(before):
        return None
    if after.stock == 0:
        return StockAlert(
            AlertKind.OUT, f"Out of stock: {after.name}", f"{after.name} is out of stock."
        )
    return StockAlert(
        AlertKind.LOW,
        f"Low stock: {after.name}",
        f"{after.name} is low: {left} left (reorder at {after.reorder_point}).",
    )


def _level(item: Item) -> int:
    """0 = fine, 1 = at or below the reorder point, 2 = none left."""
    if item.stock == 0:
        return 2
    if item.stock <= item.reorder_point:
        return 1
    return 0

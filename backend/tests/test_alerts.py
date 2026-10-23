"""Which stock changes send an email, and what it says."""

from dataclasses import replace

import pytest

from core.alerts import AlertKind, stock_alert
from core.models import Item

LIDS = Item("lids", "12 oz cup lids", "bag", stock=12, reorder_point=10)


def lids(stock: int, *, needs_count: bool = False) -> Item:
    return replace(LIDS, stock=stock, needs_count=needs_count)


def test_dropping_to_the_reorder_point_sends_a_low_alert() -> None:
    alert = stock_alert(lids(11), lids(10))

    assert alert is not None and alert.kind is AlertKind.LOW
    assert alert.subject == "Low stock: 12 oz cup lids"
    assert alert.text == "12 oz cup lids is low: 10 bags left (reorder at 10)."


def test_reaching_zero_sends_an_out_alert() -> None:
    alert = stock_alert(lids(1), lids(0))

    assert alert is not None and alert.kind is AlertKind.OUT
    assert alert.text == "12 oz cup lids is out of stock."


def test_a_scan_taking_more_than_in_stock_asks_for_a_count() -> None:
    alert = stock_alert(lids(1), lids(0, needs_count=True))

    assert alert is not None and alert.kind is AlertKind.COUNT
    assert alert.text == (
        "A scan took more 12 oz cup lids than the system had in stock. "
        "Stock is now 0 bags: please count it."
    )


@pytest.mark.parametrize(
    "before, after",
    [
        (12, 11),  # still above the reorder point
        (10, 9),  # already low: no second email for every scan
        (0, 0),  # already out
        (9, 12),  # restock or undo: going up
        (0, 5),  # partly restocked, still low: the owner knows
    ],
)
def test_no_email_unless_it_gets_worse(before: int, after: int) -> None:
    assert stock_alert(lids(before), lids(after)) is None


def test_low_then_out_sends_both() -> None:
    assert stock_alert(lids(11), lids(10)).kind is AlertKind.LOW
    assert stock_alert(lids(1), lids(0)).kind is AlertKind.OUT


def test_new_items_send_nothing() -> None:
    assert stock_alert(None, lids(0)) is None


def test_still_needing_a_count_sends_no_second_count_alert() -> None:
    assert stock_alert(lids(5, needs_count=True), lids(4, needs_count=True)) is None

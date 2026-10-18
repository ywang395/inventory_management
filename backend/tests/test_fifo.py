import pytest

from core.fifo import deduct_fifo
from core.models import Allocation
from factories import batch


def test_takes_from_oldest_expiry_first() -> None:
    batches = [
        batch("milk", "late", 5, expires_in_days=9),
        batch("milk", "early", 5, expires_in_days=2),
    ]
    deduction = deduct_fifo(batches, 3)
    assert deduction.allocations == (Allocation("early", -3),)
    assert deduction.shortfall == 0


def test_deduction_can_span_batches() -> None:
    batches = [
        batch("milk", "late", 5, expires_in_days=9),
        batch("milk", "early", 5, expires_in_days=2),
    ]
    deduction = deduct_fifo(batches, 7)
    assert deduction.allocations == (Allocation("early", -5), Allocation("late", -2))


def test_empty_batches_are_skipped() -> None:
    batches = [
        batch("milk", "early", 0, expires_in_days=2),
        batch("milk", "late", 5, expires_in_days=9),
    ]
    assert deduct_fifo(batches, 2).allocations == (Allocation("late", -2),)


def test_same_expiry_takes_batch_received_first() -> None:
    batches = [
        batch("milk", "newer", 5, expires_in_days=4, received_days_ago=1),
        batch("milk", "older", 5, expires_in_days=4, received_days_ago=3),
    ]
    assert deduct_fifo(batches, 1).allocations == (Allocation("older", -1),)


def test_batches_without_expiry_come_last() -> None:
    batches = [
        batch("milk", "no-date", 5),
        batch("milk", "dated", 5, expires_in_days=30),
    ]
    assert deduct_fifo(batches, 1).allocations == (Allocation("dated", -1),)


def test_item_without_expiry_uses_its_single_batch() -> None:
    deduction = deduct_fifo([batch("lid12", "default", 10)], 4)
    assert deduction.allocations == (Allocation("default", -4),)


def test_not_enough_stock_reports_shortfall() -> None:
    batches = [
        batch("milk", "a", 3, expires_in_days=1),
        batch("milk", "b", 2, expires_in_days=2),
    ]
    deduction = deduct_fifo(batches, 8)
    assert deduction.allocations == (Allocation("a", -3), Allocation("b", -2))
    assert deduction.shortfall == 3


@pytest.mark.parametrize("amount", [0, -1])
def test_amount_must_be_positive(amount: int) -> None:
    with pytest.raises(ValueError):
        deduct_fifo([batch("lid12", "default", 10)], amount)

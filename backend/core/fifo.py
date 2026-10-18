"""First-in, first-out deduction across an item's batches."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from core.models import Allocation, Batch


@dataclass(frozen=True)
class Deduction:
    allocations: tuple[Allocation, ...]  # negative changes, one per batch touched
    shortfall: int  # units that could not be taken because stock ran out


def fifo_order(batches: Iterable[Batch]) -> list[Batch]:
    """Oldest expiry first; ties go to the batch received first.

    Batches without an expiry date sort last.
    """
    return sorted(
        batches,
        key=lambda b: (b.expires_on or date.max, b.received_on, b.batch_id),
    )


def deduct_fifo(batches: Iterable[Batch], amount: int) -> Deduction:
    """Plan which batches to take `amount` units from. Does not change the batches."""
    if amount <= 0:
        raise ValueError("amount to deduct must be positive")

    remaining = amount
    allocations: list[Allocation] = []
    for batch in fifo_order(batches):
        if remaining == 0:
            break
        if batch.quantity <= 0:
            continue
        taken = min(batch.quantity, remaining)
        allocations.append(Allocation(batch.batch_id, -taken))
        remaining -= taken

    return Deduction(allocations=tuple(allocations), shortfall=remaining)

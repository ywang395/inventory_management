"""UNDO + code: reverse the most recent not-yet-undone scan of that code, today."""

import pytest

from core.models import Code, CodeAction, Item, LogAction, LogEntry, OutcomeStatus, ScanOutcome
from core.scan import build_undo, find_undo_target, process_scan, scan_id
from factories import (
    ANNA,
    BADGES,
    BADGES_OWN_UNDO,
    BEN,
    LID_BAG,
    LID_BOX,
    LID_RESTOCK_BOX,
    NO_BADGES,
    lids,
    scan,
    undo,
)

HOUR = 3600


def take_box(
    seconds: int, *, stock: int = 30, staff: str | None = None, station: str = "door-1"
) -> ScanOutcome:
    request = scan(LID_BOX.code, seconds=seconds, staff=staff, station=station)
    return process_scan(request, LID_BOX, lids(stock=stock), settings=NO_BADGES)


def entry_of(outcome: ScanOutcome) -> LogEntry:
    assert outcome.entry is not None
    return outcome.entry


# --- Finding which scan to undo -------------------------------------------------------


def test_undo_picks_the_most_recent_scan_of_that_code() -> None:
    first = entry_of(take_box(0))
    second = entry_of(take_box(60))

    target = find_undo_target(undo(LID_BOX.code, seconds=120), [first, second], NO_BADGES)

    assert target == second


def test_undoing_again_reaches_the_next_older_scan() -> None:
    first = entry_of(take_box(0))
    second = entry_of(take_box(60))
    undo_second = entry_of(
        build_undo(undo(LID_BOX.code, seconds=120), second, lids(), settings=NO_BADGES)
    )

    target = find_undo_target(
        undo(LID_BOX.code, seconds=180), [first, second, undo_second], NO_BADGES
    )

    assert target == first


def test_nothing_left_to_undo_once_every_scan_is_undone() -> None:
    first = entry_of(take_box(0))
    undo_first = entry_of(
        build_undo(undo(LID_BOX.code, seconds=60), first, lids(), settings=NO_BADGES)
    )

    target = find_undo_target(undo(LID_BOX.code, seconds=120), [first, undo_first], NO_BADGES)

    assert target is None


def test_only_the_exact_code_matches() -> None:
    box = entry_of(take_box(0))
    # Same item (lids) but a different code (bag), so the box scan is not undone.
    assert find_undo_target(undo(LID_BAG.code, seconds=60), [box], NO_BADGES) is None


def test_scanner_newline_and_lowercase_still_match() -> None:
    box = entry_of(take_box(0))
    request = undo("  inv-lid12-1box\n", seconds=60)
    assert find_undo_target(request, [box], NO_BADGES) == box


def test_yesterdays_scan_cannot_be_undone() -> None:
    # T0 is 10:00 in Los Angeles; 11 hours earlier is 23:00 the day before.
    yesterday = entry_of(take_box(-11 * HOUR))
    assert find_undo_target(undo(LID_BOX.code, seconds=0), [yesterday], NO_BADGES) is None


def test_early_morning_scan_counts_as_today() -> None:
    # 9 hours before T0 is 01:00 the same day in Los Angeles (but a different UTC hour).
    early = entry_of(take_box(-9 * HOUR))
    assert find_undo_target(undo(LID_BOX.code, seconds=0), [early], NO_BADGES) == early


def test_scans_after_the_undo_are_ignored() -> None:
    later = entry_of(take_box(60))
    assert find_undo_target(undo(LID_BOX.code, seconds=0), [later], NO_BADGES) is None


def test_other_stations_scans_are_ignored() -> None:
    other = entry_of(take_box(0, station="door-2"))
    request = undo(LID_BOX.code, seconds=60, station="door-1")
    assert find_undo_target(request, [other], NO_BADGES) is None


def test_undo_entries_are_never_targets() -> None:
    first = entry_of(take_box(0))
    undo_first = entry_of(
        build_undo(undo(LID_BOX.code, seconds=60), first, lids(), settings=NO_BADGES)
    )
    assert undo_first.code == LID_BOX.code  # an undo is logged with the undone code ...
    # ... but undoing again must not "undo the undo".
    target = find_undo_target(undo(LID_BOX.code, seconds=120), [undo_first], NO_BADGES)
    assert target is None


def test_anyone_can_undo_when_own_scans_only_is_off() -> None:
    annas = entry_of(take_box(0, staff=ANNA.staff_code))
    request = undo(LID_BOX.code, seconds=60, staff=BEN.staff_code)
    assert find_undo_target(request, [annas], BADGES) == annas


def test_own_scans_only_skips_other_staffs_scans() -> None:
    annas = entry_of(take_box(0, staff=ANNA.staff_code))
    bens = entry_of(take_box(60, staff=BEN.staff_code))
    request = undo(LID_BOX.code, seconds=120, staff=ANNA.staff_code)
    # Ben's scan is newer, but Anna may only undo her own.
    assert find_undo_target(request, [annas, bens], BADGES_OWN_UNDO) == annas


# --- Building the reversing entry -----------------------------------------------------


def test_undo_puts_back_what_was_taken() -> None:
    taken = take_box(0, stock=30)

    outcome = build_undo(undo(LID_BOX.code, seconds=5), taken.entry, taken.item, settings=NO_BADGES)

    assert outcome.status is OutcomeStatus.APPLIED
    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 30
    assert outcome.entry.change == 12


def test_undo_entry_points_at_the_entry_it_reverses() -> None:
    taken = take_box(0)
    request = undo(LID_BOX.code, seconds=5)

    entry = entry_of(build_undo(request, taken.entry, taken.item, settings=NO_BADGES))

    assert entry.action is LogAction.UNDO
    assert entry.code == "INV-LID12-1BOX"
    assert entry.undoes == entry_of(taken).entry_id
    assert entry.entry_id == scan_id("door-1", request.scanned_at)
    assert entry.item_id == "lid12"


def test_undo_after_a_clamped_take_restores_only_what_was_taken() -> None:
    taken = take_box(0, stock=5)  # asked for 12, had 5

    outcome = build_undo(undo(LID_BOX.code, seconds=5), taken.entry, taken.item, settings=NO_BADGES)

    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 5
    assert outcome.entry.change == 5


def test_undo_of_a_restock_already_partly_used_clamps_at_zero_and_flags_a_count() -> None:
    restocked = process_scan(
        scan(LID_RESTOCK_BOX.code), LID_RESTOCK_BOX, lids(stock=0), settings=NO_BADGES
    )
    # 8 of the 12 bags were used before the undo arrived.
    now = lids(stock=4)

    outcome = build_undo(
        undo(LID_RESTOCK_BOX.code, seconds=60), restocked.entry, now, settings=NO_BADGES
    )

    assert outcome.item is not None and outcome.entry is not None
    assert outcome.item.stock == 0
    assert outcome.item.needs_count
    assert outcome.entry.change == -4
    assert outcome.entry.shortfall == 8


def test_no_target_means_nothing_to_undo() -> None:
    outcome = build_undo(undo(LID_BOX.code, seconds=0), None, None, settings=NO_BADGES)
    assert outcome.status is OutcomeStatus.NOTHING_TO_UNDO
    assert outcome.reason == "Nothing to undo today."
    assert outcome.entry is None


def test_nothing_to_undo_message_names_the_badge() -> None:
    request = undo(LID_BOX.code, seconds=0, staff=ANNA.staff_code)
    outcome = build_undo(request, None, None, settings=BADGES, staff=ANNA)
    assert outcome.reason == "Nothing to undo for STAFF-001 today."


def test_with_badges_an_undo_without_a_badge_is_rejected() -> None:
    taken = take_box(0)
    outcome = build_undo(undo(LID_BOX.code, seconds=5), taken.entry, taken.item, settings=BADGES)
    assert outcome.status is OutcomeStatus.REJECTED
    assert outcome.item is None


def test_resent_undo_is_a_duplicate() -> None:
    taken = take_box(0)
    outcome = build_undo(
        undo(LID_BOX.code, seconds=5),
        taken.entry,
        taken.item,
        settings=NO_BADGES,
        already_seen=True,
    )
    assert outcome.status is OutcomeStatus.DUPLICATE
    assert outcome.item is None


def test_station_cannot_undo_another_stations_scan() -> None:
    taken = take_box(0, station="door-1")
    with pytest.raises(ValueError):
        build_undo(
            undo(LID_BOX.code, seconds=5, station="door-2"),
            taken.entry,
            taken.item,
            settings=NO_BADGES,
        )


def test_undo_with_the_wrong_item_is_an_error() -> None:
    taken = take_box(0)
    milk = Item(item_id="milk", name="Whole milk", smallest_unit="carton")
    with pytest.raises(ValueError):
        build_undo(undo(LID_BOX.code, seconds=5), taken.entry, milk, settings=NO_BADGES)


def test_undo_from_a_phone_matches_a_usb_scanner_scan() -> None:
    # The USB scanner sent 12 digits; the phone sends the same barcode with a 0 in front.
    upc = Code("012345678905", "lid12", -1, CodeAction.TAKE)
    scanned = entry_of(process_scan(scan(upc.code), upc, lids(), settings=NO_BADGES))

    target = find_undo_target(undo("0012345678905", seconds=60), [scanned], NO_BADGES)

    assert target == scanned

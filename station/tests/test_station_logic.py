"""The station's own rules: what each scanned code does (no network, no storage)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from logic import Outgoing, StationConfig, StationState, Step, on_scan

T0 = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)
NO_BADGES = StationConfig("Test Café", "door-1", "Storage door", "America/Los_Angeles")
BADGES = replace(NO_BADGES, require_staff_badge=True)


def run(*scans: tuple[int, str], config: StationConfig = NO_BADGES) -> list[Step]:
    """Scan codes one after another, each (`seconds` after T0, code). Returns every step."""
    state = StationState()
    steps = []
    for seconds, code in scans:
        step = on_scan(state, code, T0 + timedelta(seconds=seconds), config)
        state = step.state
        steps.append(step)
    return steps


def sent(step: Step) -> Outgoing:
    assert step.send is not None, step.message
    return step.send


# --- Plain scans ------------------------------------------------------------------------


def test_inventory_code_is_sent_with_the_time_it_was_scanned() -> None:
    (step,) = run((0, "INV-LID12-1BAG"))
    assert step.send == Outgoing("INV-LID12-1BAG", "scan", T0)


def test_codes_are_normalized_before_sending() -> None:
    lower, iphone_upc = run((0, " inv-lid12-1bag\n"), (1, "0078255001097"))
    assert sent(lower).code == "INV-LID12-1BAG"
    assert sent(iphone_upc).code == "078255001097"


def test_other_codes_are_not_sent() -> None:
    blank, url = run((0, ""), (1, "https://example.com/menu"))
    assert blank.send is None and blank.message == ""
    assert url.send is None and url.message == "Not an inventory code."


def test_body_matches_the_api() -> None:
    (step,) = run((0, "INV-LID12-1BAG"))
    assert sent(step).body() == {
        "code": "INV-LID12-1BAG",
        "action": "scan",
        "scanned_at": "2026-10-17T17:00:00.000000+00:00",
    }


# --- Undo mode --------------------------------------------------------------------------


def test_undo_then_a_code_sends_an_undo() -> None:
    undo, code = run((0, "INV-UNDO"), (3, "INV-LID12-1BAG"))
    assert undo.send is None and undo.message == "UNDO: scan the code to undo (10 s)."
    assert sent(code).action == "undo"


def test_one_undo_per_inv_undo() -> None:
    _, first, second = run((0, "INV-UNDO"), (1, "INV-LID12-1BAG"), (2, "INV-LID12-1BAG"))
    assert (sent(first).action, sent(second).action) == ("undo", "scan")


def test_undo_mode_ends_after_10_seconds() -> None:
    _, late = run((0, "INV-UNDO"), (10, "INV-LID12-1BAG"))
    assert sent(late).action == "scan"


def test_undo_again_cancels() -> None:
    _, cancel, code = run((0, "INV-UNDO"), (1, "INV-UNDO"), (2, "INV-LID12-1BAG"))
    assert cancel.message == "Undo cancelled."
    assert sent(code).action == "scan"


def test_a_badge_cancels_undo_mode() -> None:
    _, badge, code = run((0, "INV-UNDO"), (1, "STAFF-001"), (2, "INV-LID12-1BAG"))
    assert badge.message == "Badge STAFF-001 active. Undo cancelled."
    assert sent(code).action == "scan"


# --- Staff badges -----------------------------------------------------------------------


def test_badge_is_sent_with_scans() -> None:
    _, code = run((0, "STAFF-001"), (5, "INV-LID12-1BAG"))
    assert sent(code).staff == "STAFF-001"


def test_badge_stays_active_while_scanning_and_ends_after_2_idle_minutes() -> None:
    steps = run(
        (0, "STAFF-001"),
        (100, "INV-LID12-1BAG"),  # scanning keeps the badge active...
        (219, "INV-LID12-1BAG"),  # ...for 2 minutes after the last scan
        (340, "INV-LID12-1BAG"),  # 121 s without a scan: badge gone
    )
    assert [sent(step).staff for step in steps[1:]] == ["STAFF-001", "STAFF-001", None]


def test_another_badge_takes_over() -> None:
    _, _, code = run((0, "STAFF-001"), (1, "STAFF-002"), (2, "INV-LID12-1BAG"))
    assert sent(code).staff == "STAFF-002"


def test_shop_that_requires_badges_needs_one_before_scanning() -> None:
    no_badge, undo, badge, code = run(
        (0, "INV-LID12-1BAG"), (1, "INV-UNDO"), (2, "STAFF-001"), (3, "INV-LID12-1BAG"),
        config=BADGES,
    )  # fmt: skip
    assert no_badge.send is None and no_badge.message == "Scan your staff badge first."
    assert undo.state.undo_until is None and undo.message == "Scan your staff badge first."
    assert sent(code).staff == "STAFF-001"


def test_shop_that_requires_badges_stops_after_the_badge_times_out() -> None:
    _, late = run((0, "STAFF-001"), (120, "INV-LID12-1BAG"), config=BADGES)
    assert late.send is None and late.message == "Scan your staff badge first."

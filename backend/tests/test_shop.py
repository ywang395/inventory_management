from datetime import UTC, datetime, timedelta

import pytest

from core.models import ShopSettings, Staff
from core.shop import shop_day_bounds, staff_problem
from factories import ANNA, BADGES, NO_BADGES, SHOP_TZ, T0

# --- Settings -------------------------------------------------------------------------


def test_own_scans_only_needs_badges() -> None:
    with pytest.raises(ValueError, match="require_staff_badge"):
        ShopSettings(timezone=SHOP_TZ, undo_own_scans_only=True)


def test_unknown_time_zone_is_rejected() -> None:
    with pytest.raises(ValueError, match="time zone"):
        ShopSettings(timezone="Mars/Olympus_Mons")


# --- What "today" means for the shop --------------------------------------------------


def test_shop_day_runs_from_local_midnight_to_midnight() -> None:
    start, end = shop_day_bounds(T0, NO_BADGES)
    # Oct 17 in Los Angeles (UTC-7 in summer time) = 07:00 UTC to 07:00 UTC next day.
    assert start == datetime(2026, 10, 17, 7, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 18, 7, 0, tzinfo=UTC)


def test_shop_day_is_25_hours_when_clocks_go_back() -> None:
    # Summer time ends in the US on Sunday Nov 1, 2026.
    start, end = shop_day_bounds(datetime(2026, 11, 1, 20, 0, tzinfo=UTC), NO_BADGES)
    assert end - start == timedelta(hours=25)


# --- Staff badges ---------------------------------------------------------------------


def test_without_badges_anything_goes() -> None:
    assert staff_problem(NO_BADGES, None, None) == ""


@pytest.mark.parametrize(
    "staff_code, staff, problem",
    [
        (None, None, "Scan your staff badge first."),
        ("STAFF-999", None, "Unknown staff badge STAFF-999."),
        ("STAFF-001", Staff("STAFF-001", "Anna", active=False), "no longer active"),
        ("STAFF-001", Staff("STAFF-002", "Ben"), "Unknown staff badge STAFF-001."),
    ],
)
def test_with_badges_bad_badges_are_explained(
    staff_code: str | None, staff: Staff | None, problem: str
) -> None:
    assert problem in staff_problem(BADGES, staff_code, staff)


def test_with_badges_an_active_badge_is_fine() -> None:
    assert staff_problem(BADGES, "STAFF-001", ANNA) == ""

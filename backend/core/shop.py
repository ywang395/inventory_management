"""Rules that depend on the shop owner's settings."""

from datetime import UTC, datetime, time, timedelta

from core.models import ShopSettings, Staff


def shop_day_bounds(moment: datetime, settings: ShopSettings) -> tuple[datetime, datetime]:
    """Start and end (in UTC) of the shop's local day that contains `moment`.

    Handlers use this to read only today's log entries. Days are not always 24 hours:
    on daylight-saving changes the local day is 23 or 25 hours long.
    """
    local_date = moment.astimezone(settings.zone).date()
    start = datetime.combine(local_date, time.min, tzinfo=settings.zone)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=settings.zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def same_shop_day(a: datetime, b: datetime, settings: ShopSettings) -> bool:
    return a.astimezone(settings.zone).date() == b.astimezone(settings.zone).date()


def staff_problem(settings: ShopSettings, staff_code: str | None, staff: Staff | None) -> str:
    """Why this scan's badge isn't acceptable, or "" if it's fine.

    `staff` is the Staff record found for `staff_code` (None if there is none).
    When the shop doesn't use badges, any scan is fine.
    """
    if not settings.require_staff_badge:
        return ""
    if not staff_code:
        return "Scan your staff badge first."
    if staff is None or staff.staff_code != staff_code:
        return f"Unknown staff badge {staff_code}."
    if not staff.active:
        return f"Staff badge {staff_code} is no longer active."
    return ""

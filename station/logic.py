"""What the station does with each scanned code: pure rules, no network, no storage.

A scanner types a code and Enter; on_scan() decides what to show and whether a scan
must be sent. The API makes the real decisions (stock, which scan an undo reverses,
whether a badge is valid). The station only keeps its own two modes:

- Undo mode: INV-UNDO, then within 10 s the code to undo. One undo per INV-UNDO;
  another INV-UNDO or a staff badge cancels it.
- Staff badge: a STAFF- code stays active until another badge is scanned, or 2 minutes
  pass without a scan. If the shop requires badges, codes scanned without an active
  badge aren't sent (the API would reject them anyway; this way staff know right away).
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from core.codes import CodeKind, classify_code, normalize_code
from core.scan import format_utc

UNDO_WINDOW = timedelta(seconds=10)
BADGE_TIMEOUT = timedelta(minutes=2)


@dataclass(frozen=True)
class StationConfig:
    """Shop settings from GET /station/config (cached, so the station also starts offline)."""

    shop_name: str
    station_id: str
    station_name: str
    timezone: str
    require_staff_badge: bool = False
    undo_own_scans_only: bool = False

    @classmethod
    def from_api(cls, body: dict) -> "StationConfig":
        return cls(
            shop_name=body["shop_name"],
            station_id=body["station_id"],
            station_name=body["station_name"],
            timezone=body["timezone"],
            require_staff_badge=bool(body.get("require_staff_badge", False)),
            undo_own_scans_only=bool(body.get("undo_own_scans_only", False)),
        )


@dataclass(frozen=True)
class Outgoing:
    """A scan for POST /scan.

    `scanned_at` is set once, when the code is scanned, and reused on every resend: the
    API uses station + scanned_at to recognise a resent scan and ignore it.
    """

    code: str
    action: str  # "scan" or "undo"
    scanned_at: datetime
    staff: str | None = None

    def body(self) -> dict:
        body = {"code": self.code, "action": self.action, "scanned_at": format_utc(self.scanned_at)}
        if self.staff:
            body["staff"] = self.staff
        return body


@dataclass(frozen=True)
class StationState:
    undo_until: datetime | None = None
    badge: str | None = None
    badge_until: datetime | None = None

    def at(self, now: datetime) -> "StationState":
        """This state at `now`, with modes that have run out switched off."""
        state = self
        if state.undo_until and now >= state.undo_until:
            state = replace(state, undo_until=None)
        if state.badge_until and now >= state.badge_until:
            state = replace(state, badge=None, badge_until=None)
        return state


@dataclass(frozen=True)
class Step:
    """What one scan leads to: the new state, a line to show, maybe a scan to send."""

    state: StationState
    message: str = ""
    send: Outgoing | None = None


def on_scan(state: StationState, raw: str, now: datetime, config: StationConfig) -> Step:
    code = normalize_code(raw)
    state = state.at(now)
    if not code:
        return Step(state)  # an empty Enter

    kind = classify_code(code)
    if kind is CodeKind.STAFF:
        cancelled = " Undo cancelled." if state.undo_until else ""
        state = replace(state, undo_until=None, badge=code, badge_until=now + BADGE_TIMEOUT)
        return Step(state, f"Badge {code} active.{cancelled}")
    if kind is CodeKind.IGNORED:
        return Step(state, "Not an inventory code.")
    if config.require_staff_badge and not state.badge:
        return Step(state, "Scan your staff badge first.")

    if kind is CodeKind.UNDO:
        if state.undo_until:
            return Step(replace(state, undo_until=None), "Undo cancelled.")
        undo_until = now + UNDO_WINDOW
        return Step(replace(state, undo_until=undo_until), "UNDO: scan the code to undo (10 s).")

    # An inventory code or a supplier barcode: send it (as an undo if undo mode is on).
    action = "undo" if state.undo_until else "scan"
    state = replace(state, undo_until=None)
    if state.badge:
        state = replace(state, badge_until=now + BADGE_TIMEOUT)  # every scan keeps it active
    return Step(state, send=Outgoing(code, action, now, state.badge))

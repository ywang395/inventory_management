"""The whole station (rules + outbox + sending), with a fake API that can go offline."""

from datetime import UTC, datetime, timedelta

from api import Unreachable
from logic import Outgoing, StationConfig
from outbox import Outbox
from scanner_sim import Station

T0 = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)
CONFIG = StationConfig("Test Café", "door-1", "Storage door", "America/Los_Angeles")


class FakeApi:
    def __init__(self) -> None:
        self.online = True
        self.received: list[Outgoing] = []

    def send(self, scan: Outgoing) -> str:
        if not self.online:
            raise Unreachable("no internet")
        self.received.append(scan)
        return f"{scan.action} {scan.code} ok"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)  # each scan a second later
        return self.now


def make_station() -> tuple[Station, FakeApi]:
    api = FakeApi()
    return Station(CONFIG, Outbox(":memory:"), api.send, clock=Clock()), api


def test_online_scan_shows_the_apis_answer() -> None:
    station, api = make_station()

    assert station.scan("INV-LID12-1BAG") == ["scan INV-LID12-1BAG ok"]
    assert station.outbox.count() == 0


def test_offline_scans_wait_and_go_out_later_unchanged_and_in_order() -> None:
    station, api = make_station()
    api.online = False

    assert station.scan("INV-A") == ["No connection: saved, 1 waiting."]
    assert station.scan("INV-B") == ["No connection: saved, 2 waiting."]
    assert station.retry() == []  # still offline: nothing sent, nothing lost

    api.online = True
    assert station.retry() == ["scan INV-A ok", "scan INV-B ok"]
    assert [scan.code for scan in api.received] == ["INV-A", "INV-B"]
    # Sent with the time they were scanned, not the time they finally went out.
    assert [scan.scanned_at for scan in api.received] == [
        T0 + timedelta(seconds=1),
        T0 + timedelta(seconds=2),
    ]
    assert station.outbox.count() == 0


def test_next_scan_also_sends_what_was_waiting() -> None:
    station, api = make_station()
    api.online = False
    station.scan("INV-A")

    api.online = True
    assert station.scan("INV-B") == ["scan INV-A ok", "scan INV-B ok"]


def test_undo_mode_sends_an_undo() -> None:
    station, api = make_station()

    assert station.scan("INV-UNDO") == ["UNDO: scan the code to undo (10 s)."]
    assert station.scan("INV-LID12-1BAG") == ["undo INV-LID12-1BAG ok"]

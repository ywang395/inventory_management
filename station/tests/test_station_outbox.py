"""The offline queue (SQLite): scans wait here, in order, until the API has answered."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from logic import Outgoing
from outbox import Outbox

T0 = datetime(2026, 10, 17, 17, 0, 0, 123456, tzinfo=UTC)  # microseconds matter


def scan(seconds: int = 0, code: str = "INV-LID12-1BAG") -> Outgoing:
    return Outgoing(code, "scan", T0 + timedelta(seconds=seconds), staff="STAFF-001")


def test_a_scan_comes_back_exactly_as_saved() -> None:
    outbox = Outbox(":memory:")
    outbox.add(scan())

    ((_, saved),) = outbox.pending()

    assert saved == scan()
    assert saved.body()["scanned_at"] == "2026-10-17T17:00:00.123456+00:00"


def test_oldest_first_and_removed_once_sent() -> None:
    outbox = Outbox(":memory:")
    first = outbox.add(scan(0, "INV-A"))
    outbox.add(scan(1, "INV-B"))

    assert [s.code for _, s in outbox.pending()] == ["INV-A", "INV-B"]
    outbox.remove(first)
    assert [s.code for _, s in outbox.pending()] == ["INV-B"]
    assert outbox.count() == 1


def test_waiting_scans_survive_a_restart(tmp_path: Path) -> None:
    Outbox(tmp_path / "station.db").add(scan())

    restarted = Outbox(tmp_path / "station.db")

    assert [s for _, s in restarted.pending()] == [scan()]


def test_shop_settings_are_saved_for_offline_starts(tmp_path: Path) -> None:
    assert Outbox(tmp_path / "station.db").load_config() is None
    Outbox(tmp_path / "station.db").save_config({"shop_name": "Test Café"})

    assert Outbox(tmp_path / "station.db").load_config() == {"shop_name": "Test Café"}

"""POST /scan end to end: API Gateway event -> handler -> moto's fake DynamoDB."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import DOOR_KEY, TENANT
from core.models import Item, Staff
from core.scan import format_utc
from factories import ANNA, BADGES, BADGES_OWN_UNDO, T0
from handlers.scan import MAX_ATTEMPTS, handle
from store.dynamo import DynamoStore, Shop

NOW = T0 + timedelta(minutes=1)


def call(store: DynamoStore, body, *, key: str | None = DOOR_KEY) -> tuple[int, dict]:
    event = {
        "requestContext": {"identity": {"apiKeyId": key}},
        "body": body if isinstance(body, str) or body is None else json.dumps(body),
    }
    reply = handle(event, store, now=NOW)
    return reply["statusCode"], json.loads(reply["body"])


def scan_body(code: str, seconds: int = 0, **extra) -> dict:
    return {"code": code, "scanned_at": format_utc(T0 + timedelta(seconds=seconds)), **extra}


def stock(store: DynamoStore) -> int:
    item = store.get_item(TENANT, "lid12")
    assert item is not None
    return item.stock


def use_settings(store: DynamoStore, settings) -> None:
    store.put_shop(Shop(tenant_id=TENANT, name="Test Café", settings=settings))


# --- Taking and restocking ------------------------------------------------------------


def test_a_scan_changes_stock_in_the_database(store: DynamoStore) -> None:
    status, body = call(store, scan_body("INV-LID12-1BOX"))

    assert status == 200
    assert body["status"] == "applied"
    assert body["message"] == "12 oz lids -12 bag, 18 left."
    assert body["item"]["stock"] == 18
    assert stock(store) == 18


def test_restock_adds_stock(store: DynamoStore) -> None:
    call(store, scan_body("INV-LID12-RESTOCK-BOX"))
    assert stock(store) == 42


def test_taking_more_than_stock_asks_for_a_count(store: DynamoStore) -> None:
    store.put_item(TENANT, Item("lid12", "12 oz lids", "bag", stock=5))

    _, body = call(store, scan_body("INV-LID12-1BOX"))

    assert body["item"] == {
        "item_id": "lid12",
        "name": "12 oz lids",
        "stock": 0,
        "needs_count": True,
    }
    assert "7 more than in stock" in body["message"]


def test_resent_scan_is_counted_once(store: DynamoStore) -> None:
    call(store, scan_body("INV-LID12-1BOX"))

    status, body = call(store, scan_body("INV-LID12-1BOX"))

    assert (status, body["status"], body["message"]) == (200, "duplicate", "Already recorded.")
    assert stock(store) == 18


def test_station_comes_from_the_api_key_not_the_body(store: DynamoStore) -> None:
    call(store, scan_body("INV-LID12-1BOX", station="door-99", tenant_id="cafe-99"))

    assert store.log_entry_exists(TENANT, "door-1", T0)
    assert not store.log_entry_exists(TENANT, "door-99", T0)


# --- Codes that don't change stock ----------------------------------------------------


@pytest.mark.parametrize("code", ["hello", "STAFF-001", "INV-UNDO"])
def test_non_inventory_codes_are_ignored(store: DynamoStore, code: str) -> None:
    _, body = call(store, scan_body(code))
    assert body == {"status": "ignored", "message": "Not an inventory code."}
    assert stock(store) == 30


def test_unknown_inventory_code_is_ignored(store: DynamoStore) -> None:
    _, body = call(store, scan_body("INV-NOT-ON-THE-SHEET"))
    assert body["status"] == "ignored"
    assert "Unknown code" in body["message"]


# --- Undo -----------------------------------------------------------------------------


def test_undo_reverses_todays_scan_of_that_code(store: DynamoStore) -> None:
    call(store, scan_body("INV-LID12-1BOX"))

    _, body = call(store, scan_body("INV-LID12-1BOX", seconds=30, action="undo"))

    assert body["status"] == "applied"
    assert body["message"] == "Undone: 12 oz lids +12 bag, 30 left."
    assert stock(store) == 30


def test_undo_twice_reaches_the_older_scan_then_nothing(store: DynamoStore) -> None:
    call(store, scan_body("INV-LID12-1BAG", seconds=0))
    call(store, scan_body("INV-LID12-1BAG", seconds=10))  # stock 28

    call(store, scan_body("INV-LID12-1BAG", seconds=20, action="undo"))
    call(store, scan_body("INV-LID12-1BAG", seconds=30, action="undo"))
    _, body = call(store, scan_body("INV-LID12-1BAG", seconds=40, action="undo"))

    assert stock(store) == 30
    assert (body["status"], body["message"]) == ("nothing_to_undo", "Nothing to undo today.")


def test_resent_undo_is_counted_once(store: DynamoStore) -> None:
    call(store, scan_body("INV-LID12-1BAG", seconds=0))
    call(store, scan_body("INV-LID12-1BAG", seconds=10))
    call(store, scan_body("INV-LID12-1BAG", seconds=20, action="undo"))

    _, body = call(store, scan_body("INV-LID12-1BAG", seconds=20, action="undo"))

    assert body["status"] == "duplicate"
    assert stock(store) == 29  # only one of the two bags was put back


# --- Staff badges ---------------------------------------------------------------------


def test_badge_required_rejects_a_scan_without_one(store: DynamoStore) -> None:
    use_settings(store, BADGES)

    _, body = call(store, scan_body("INV-LID12-1BOX"))

    assert (body["status"], body["message"]) == ("rejected", "Scan your staff badge first.")
    assert stock(store) == 30


def test_badge_required_accepts_an_active_badge(store: DynamoStore) -> None:
    use_settings(store, BADGES)

    _, body = call(store, scan_body("INV-LID12-1BOX", staff=" staff-001 "))

    assert body["status"] == "applied"
    assert stock(store) == 18


def test_badge_required_rejects_an_inactive_badge(store: DynamoStore) -> None:
    use_settings(store, BADGES)
    store.put_staff(TENANT, Staff("STAFF-003", "Cleo", active=False))

    _, body = call(store, scan_body("INV-LID12-1BOX", staff="STAFF-003"))

    assert body["status"] == "rejected"


def test_own_scans_only_blocks_undoing_a_coworkers_scan(store: DynamoStore) -> None:
    use_settings(store, BADGES_OWN_UNDO)
    store.put_staff(TENANT, Staff("STAFF-002", "Ben"))
    call(store, scan_body("INV-LID12-1BOX", staff=ANNA.staff_code))

    _, body = call(store, scan_body("INV-LID12-1BOX", seconds=30, action="undo", staff="STAFF-002"))

    assert (body["status"], body["message"]) == (
        "nothing_to_undo",
        "Nothing to undo for STAFF-002 today.",
    )
    assert stock(store) == 18


# --- Two stations at once -------------------------------------------------------------


class BusyStore(DynamoStore):
    """Another station takes one bag right after each of our stock reads."""

    def __init__(self, store: DynamoStore, times: int) -> None:
        super().__init__(store.tables)
        self.client = store.client
        self.times = times

    def get_item(self, tenant_id: str, item_id: str):
        item = super().get_item(tenant_id, item_id)
        if item is not None and self.times > 0:
            self.times -= 1
            self.put_item(tenant_id, replace(item, stock=item.stock - 1))
        return item  # the value we read is now out of date


def test_stock_change_by_another_station_is_retried(store: DynamoStore) -> None:
    busy = BusyStore(store, times=1)

    _, body = call(busy, scan_body("INV-LID12-1BOX"))

    assert body["status"] == "applied"
    assert stock(store) == 17  # 30 - 1 (other station) - 12 (us): nothing lost


def test_gives_up_after_too_many_conflicts(store: DynamoStore) -> None:
    busy = BusyStore(store, times=MAX_ATTEMPTS)

    status, body = call(busy, scan_body("INV-LID12-1BOX"))

    assert (status, body["status"]) == (503, "busy")
    assert not store.log_entry_exists(TENANT, "door-1", T0)


# --- Bad requests ---------------------------------------------------------------------


def test_unknown_api_key_is_refused(store: DynamoStore) -> None:
    status, body = call(store, scan_body("INV-LID12-1BOX"), key="someone-elses-key")
    assert (status, body["message"]) == (403, "This station is not registered.")


@pytest.mark.parametrize(
    "body, message",
    [
        ("not json", "Body must be JSON."),
        ([1, 2], "Body must be a JSON object."),
        ({"scanned_at": "2026-10-17T17:00:00+00:00"}, "code is required."),
        ({"code": "INV-LID12-1BAG"}, "scanned_at is required."),
        ({"code": "INV-LID12-1BAG", "scanned_at": "yesterday"}, "ISO 8601"),
        ({"code": "INV-LID12-1BAG", "scanned_at": "2026-10-17T17:00:00"}, "time zone"),
        (
            {"code": "INV-LID12-1BAG", "scanned_at": "2026-10-17T17:00:00+00:00", "action": "x"},
            "action must be",
        ),
    ],
)
def test_bad_requests_are_explained(store: DynamoStore, body, message: str) -> None:
    status, reply = call(store, body)
    assert status == 400
    assert message in reply["message"]


def test_scan_from_the_future_is_refused(store: DynamoStore) -> None:
    status, body = call(store, scan_body("INV-LID12-1BAG", seconds=3600))
    assert status == 400
    assert "station clock" in body["message"]

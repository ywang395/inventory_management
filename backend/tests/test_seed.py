"""The seed script, against moto's fake DynamoDB (no AWS involved)."""

import json
from datetime import timedelta

from conftest import DOOR_KEY
from core.scan import format_utc
from factories import T0
from handlers.scan import handle
from seed import CODES, ITEMS, STAFF, TENANT, seed, table_names
from store.dynamo import DynamoStore


def scan(store: DynamoStore, code: str, seconds: int = 0) -> dict:
    body = {"code": code, "scanned_at": format_utc(T0 + timedelta(seconds=seconds))}
    event = {"requestContext": {"identity": {"apiKeyId": DOOR_KEY}}, "body": json.dumps(body)}
    return json.loads(handle(event, store, now=T0 + timedelta(minutes=1))["body"])


def test_seed_creates_the_test_shop(empty_store: DynamoStore) -> None:
    seed(empty_store, DOOR_KEY)

    station = empty_store.get_station(DOOR_KEY)
    assert station is not None
    assert (station.tenant_id, station.station_id) == (TENANT, "door-1")
    shop = empty_store.get_shop(TENANT)
    assert shop is not None
    assert shop.settings.timezone == "America/Los_Angeles"
    assert not shop.settings.require_staff_badge
    for staff in STAFF:
        assert empty_store.get_staff(TENANT, staff.staff_code) == staff


def test_every_code_points_to_a_seeded_item(empty_store: DynamoStore) -> None:
    seed(empty_store, DOOR_KEY)
    item_ids = {item.item_id for item in ITEMS}
    for code in CODES:
        assert code.item_id in item_ids, code.code
        assert empty_store.get_code(TENANT, code.code) == code


def test_a_scan_works_on_the_seeded_shop(empty_store: DynamoStore) -> None:
    seed(empty_store, DOOR_KEY)

    reply = scan(empty_store, "INV-LID12-1BOX")

    assert reply["status"] == "applied"
    assert reply["message"] == "12 oz cup lids -12 bag, 18 left."


def test_supplier_barcode_takes_one_carton(empty_store: DynamoStore) -> None:
    seed(empty_store, DOOR_KEY)

    reply = scan(empty_store, "012345678905")

    assert reply["message"] == "Oat milk -1 carton, 17 left."


def test_seeding_again_keeps_stock(empty_store: DynamoStore) -> None:
    seed(empty_store, DOOR_KEY)
    scan(empty_store, "INV-LID12-1BOX")

    report = seed(empty_store, DOOR_KEY)

    assert empty_store.get_item(TENANT, "lids_12oz").stock == 18
    assert "lids_12oz: kept stock 18 bag" in report


def test_reset_stock_puts_sample_amounts_back(empty_store: DynamoStore) -> None:
    seed(empty_store, DOOR_KEY)
    scan(empty_store, "INV-LID12-1BOX")

    seed(empty_store, DOOR_KEY, reset_stock=True)

    assert empty_store.get_item(TENANT, "lids_12oz").stock == 30


def test_table_names_come_from_stack_outputs() -> None:
    outputs = {
        "ShopsTableName": "s",
        "ItemsTableName": "i",
        "CodesTableName": "c",
        "StaffTableName": "st",
        "StationsTableName": "sn",
        "ScanLogTableName": "l",
    }
    names = table_names(outputs)
    assert (names.items, names.scan_log) == ("i", "l")

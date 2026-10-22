"""The DynamoDB data layer, against moto's fake DynamoDB."""

from dataclasses import replace
from datetime import timedelta

from conftest import DOOR_KEY, TENANT
from core.models import Item, LogAction
from core.scan import process_scan
from factories import ANNA, LID_BOX, NO_BADGES, T0, lids, scan
from store.dynamo import DynamoStore, SaveResult


def take_box(store: DynamoStore, seconds: int = 0):
    item = store.get_item(TENANT, "lid12")
    assert item is not None
    outcome = process_scan(scan(LID_BOX.code, seconds=seconds), LID_BOX, item, settings=NO_BADGES)
    return outcome, item.stock


def test_reads_back_what_was_written(store: DynamoStore) -> None:
    station = store.get_station(DOOR_KEY)
    assert station is not None
    assert (station.tenant_id, station.station_id) == (TENANT, "door-1")

    shop = store.get_shop(TENANT)
    assert shop is not None
    assert shop.settings == NO_BADGES

    assert store.get_code(TENANT, LID_BOX.code) == LID_BOX
    assert store.get_item(TENANT, "lid12") == lids(stock=30)
    assert store.get_staff(TENANT, ANNA.staff_code) == ANNA


def test_missing_records_are_none(store: DynamoStore) -> None:
    assert store.get_station("no-such-key") is None
    assert store.get_shop("cafe-99") is None
    assert store.get_code(TENANT, "INV-NOPE") is None
    assert store.get_item(TENANT, "nope") is None
    assert store.get_staff(TENANT, "STAFF-999") is None


def test_save_writes_the_log_entry_and_the_new_stock(store: DynamoStore) -> None:
    outcome, stock_before = take_box(store)

    assert store.save(TENANT, outcome, expected_stock=stock_before) is SaveResult.SAVED

    assert store.get_item(TENANT, "lid12").stock == 18
    assert store.log_entry_exists(TENANT, "door-1", T0)
    (entry,) = store.station_log(TENANT, "door-1", T0 - timedelta(hours=1), T0)
    assert entry == outcome.entry


def test_saving_the_same_scan_twice_is_a_duplicate(store: DynamoStore) -> None:
    outcome, stock_before = take_box(store)
    store.save(TENANT, outcome, expected_stock=stock_before)

    # The offline queue resends the same scan: same station + scanned_at.
    resent, stock_now = take_box(store)
    assert store.save(TENANT, resent, expected_stock=stock_now) is SaveResult.DUPLICATE
    assert store.get_item(TENANT, "lid12").stock == 18  # counted once


def test_save_refuses_if_stock_changed_since_it_was_read(store: DynamoStore) -> None:
    outcome, stock_before = take_box(store)
    # Another station takes a bag between our read and our write.
    store.put_item(TENANT, replace(store.get_item(TENANT, "lid12"), stock=29))

    assert store.save(TENANT, outcome, expected_stock=stock_before) is SaveResult.STOCK_CHANGED
    assert store.get_item(TENANT, "lid12").stock == 29  # nothing saved
    assert not store.log_entry_exists(TENANT, "door-1", T0)


def test_station_log_only_returns_the_asked_time_range(store: DynamoStore) -> None:
    for seconds in (0, 60, 120):
        outcome, stock = take_box(store, seconds)
        store.save(TENANT, outcome, expected_stock=stock)

    entries = store.station_log(
        TENANT, "door-1", T0 + timedelta(seconds=30), T0 + timedelta(seconds=120)
    )

    assert [e.timestamp for e in entries] == [
        T0 + timedelta(seconds=60),
        T0 + timedelta(seconds=120),
    ]
    assert all(e.action is LogAction.TAKE for e in entries)


def test_log_records_can_be_read_by_item(store: DynamoStore) -> None:
    outcome, stock = take_box(store)
    store.save(TENANT, outcome, expected_stock=stock)

    response = store.client.query(
        TableName=store.tables.scan_log,
        IndexName="by_item",
        KeyConditionExpression="item_pk = :pk",
        ExpressionAttributeValues={":pk": "TENANT#cafe-01#ITEM#lid12"},
    )
    assert len(response["Items"]) == 1


def test_list_items_returns_only_this_shops_items(store: DynamoStore) -> None:
    cups = Item("cup12", "12 oz cups", "sleeve", stock=40)
    store.put_item(TENANT, cups)
    store.put_item("cafe-02", Item("lid12", "Other shop's lids", "bag", stock=5))

    items = store.list_items(TENANT)

    assert sorted(items, key=lambda i: i.item_id) == [cups, lids(stock=30)]

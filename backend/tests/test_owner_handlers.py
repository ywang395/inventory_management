"""The owner API for the dashboard: GET /items and GET /log, shop taken from the login."""

import json
from datetime import timedelta

import pytest

from conftest import DOOR_KEY, TENANT
from core.models import Item
from core.scan import format_utc
from factories import T0
from handlers import items, log
from handlers.scan import handle as station_scan_handler
from store.dynamo import DynamoStore

DASHBOARD = "http://localhost:5173"


@pytest.fixture(autouse=True)
def dashboard_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_ORIGINS", DASHBOARD)


def owner_event(
    tenant: str | None = TENANT, *, params: dict | None = None, origin: str = DASHBOARD
) -> dict:
    """What API Gateway passes on after its Cognito authorizer accepted the ID token."""
    claims = {"email": "owner@example.com"}
    if tenant:
        claims["custom:tenant_id"] = tenant
    return {
        "requestContext": {"authorizer": {"claims": claims}},
        "headers": {"origin": origin},
        "queryStringParameters": params,
    }


def body(reply: dict) -> dict:
    return json.loads(reply["body"])


def station_scan(store: DynamoStore, code: str, seconds: int, action: str = "scan") -> None:
    """A real scan through the station API, so the log holds real entries."""
    scan = {
        "code": code,
        "action": action,
        "scanned_at": format_utc(T0 + timedelta(seconds=seconds)),
    }
    event = {"requestContext": {"identity": {"apiKeyId": DOOR_KEY}}, "body": json.dumps(scan)}
    reply = station_scan_handler(event, store, now=T0 + timedelta(minutes=10))
    assert body(reply)["status"] == "applied", body(reply)


# --- GET /items -------------------------------------------------------------------------


def test_items_lists_the_shop_with_stock_and_status(store: DynamoStore) -> None:
    store.put_item(TENANT, Item("cup12", "12 oz cups", "sleeve", stock=3, reorder_point=10))

    reply = items.handle(owner_event(), store)

    assert reply["statusCode"] == 200
    result = body(reply)
    assert result["shop"] == {"name": "Test Café", "timezone": "America/Los_Angeles"}
    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id["cup12"]["status"] == "low"
    assert by_id["lid12"] == {
        "item_id": "lid12",
        "name": "12 oz lids",
        "category": "",
        "smallest_unit": "bag",
        "stock": 30,
        "reorder_point": 0,
        "needs_count": False,
        "status": "ok",
    }


def test_owners_only_see_their_own_shop(store: DynamoStore) -> None:
    store.put_item("cafe-02", Item("milk", "Other shop's milk", "gallon", stock=4))

    mine = [item["item_id"] for item in body(items.handle(owner_event(), store))["items"]]
    theirs = [i["item_id"] for i in body(items.handle(owner_event("cafe-02"), store))["items"]]

    assert mine == ["lid12"]
    assert theirs == ["milk"]


def test_a_login_without_a_shop_gets_nothing(store: DynamoStore) -> None:
    reply = items.handle(owner_event(tenant=None), store)

    assert reply["statusCode"] == 403
    assert body(reply)["message"] == "This login isn't linked to a shop."


def test_only_the_dashboard_may_read_replies_in_a_browser(store: DynamoStore) -> None:
    allowed = items.handle(owner_event(), store)
    other = items.handle(owner_event(origin="https://evil.example"), store)

    assert allowed["headers"]["Access-Control-Allow-Origin"] == DASHBOARD
    assert "Access-Control-Allow-Origin" not in other["headers"]


# --- GET /log ---------------------------------------------------------------------------


def test_log_is_newest_first_and_marks_undone_scans(store: DynamoStore) -> None:
    station_scan(store, "INV-LID12-1BAG", 0)
    station_scan(store, "INV-LID12-1BOX", 60)
    station_scan(store, "INV-LID12-1BOX", 120, action="undo")

    reply = log.handle(owner_event(params={"item": "lid12"}), store)

    assert reply["statusCode"] == 200
    result = body(reply)
    assert result["item"]["stock"] == 29
    assert [(e["action"], e["change"], e["undone"]) for e in result["entries"]] == [
        ("undo", 12, False),
        ("take", -12, True),  # reversed by the undo above
        ("take", -1, False),
    ]
    assert result["entries"][-1]["scanned_at"] == "2026-10-17T17:00:00.000000+00:00"


def test_log_limit(store: DynamoStore) -> None:
    for seconds in range(3):
        station_scan(store, "INV-LID12-1BAG", seconds)

    entries = body(log.handle(owner_event(params={"item": "lid12", "limit": "2"}), store))[
        "entries"
    ]

    assert [e["scanned_at"][17:19] for e in entries] == ["02", "01"]


@pytest.mark.parametrize(
    "params, status",
    [
        (None, 400),  # no item
        ({"item": "lid12", "limit": "lots"}, 400),
        ({"item": "lid#12"}, 400),  # can't be a key
        ({"item": "nope"}, 404),
    ],
)
def test_log_rejects_bad_requests(store: DynamoStore, params: dict | None, status: int) -> None:
    assert log.handle(owner_event(params=params), store)["statusCode"] == status


def test_log_of_another_shops_item_is_unknown(store: DynamoStore) -> None:
    reply = log.handle(owner_event("cafe-02", params={"item": "lid12"}), store)

    assert reply["statusCode"] == 404

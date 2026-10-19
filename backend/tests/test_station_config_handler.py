import json

from conftest import DOOR_KEY
from handlers.station_config import handle
from store.dynamo import DynamoStore


def call(store: DynamoStore, key: str) -> tuple[int, dict]:
    reply = handle({"requestContext": {"identity": {"apiKeyId": key}}}, store)
    return reply["statusCode"], json.loads(reply["body"])


def test_station_gets_its_shop_settings(store: DynamoStore) -> None:
    status, body = call(store, DOOR_KEY)
    assert status == 200
    assert body == {
        "shop_name": "Test Café",
        "station_id": "door-1",
        "station_name": "Back door",
        "timezone": "America/Los_Angeles",
        "require_staff_badge": False,
        "undo_own_scans_only": False,
    }


def test_unknown_api_key_is_refused(store: DynamoStore) -> None:
    status, _ = call(store, "someone-elses-key")
    assert status == 403

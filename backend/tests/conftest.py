"""Test fixtures: a fake DynamoDB (moto) with the same tables as the CDK stack.

moto intercepts every AWS call in these tests, so nothing touches your real account.
"""

import boto3
import pytest
from moto import mock_aws

from core.models import Item
from factories import ANNA, LID_BAG, LID_BOX, LID_RESTOCK_BOX, NO_BADGES
from store import keys
from store.dynamo import DynamoStore, Shop, Station, TableNames

TENANT = "cafe-01"
DOOR_KEY = "key-door-1"  # API key id of the test station
TABLES = TableNames(
    shops="shops",
    items="items",
    codes="codes",
    staff="staff",
    stations="stations",
    scan_log="scan_log",
)


@pytest.fixture
def fake_aws(monkeypatch: pytest.MonkeyPatch):
    # Fake credentials so nothing can reach a real account, even by mistake.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-2")


@pytest.fixture
def empty_store(fake_aws) -> DynamoStore:
    client = fake_aws.meta.client
    for name in (TABLES.shops, TABLES.stations):
        _create_table(client, name, with_sort_key=False)
    for name in (TABLES.items, TABLES.codes, TABLES.staff):
        _create_table(client, name)
    _create_table(
        client,
        TABLES.scan_log,
        GlobalSecondaryIndexes=[
            {
                "IndexName": keys.BY_ITEM_INDEX,
                "KeySchema": [
                    {"AttributeName": keys.ITEM_PK, "KeyType": "HASH"},
                    {"AttributeName": keys.SK, "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        extra_attributes=[keys.ITEM_PK],
    )
    return DynamoStore(TABLES, fake_aws)


@pytest.fixture
def store(empty_store: DynamoStore) -> DynamoStore:
    """A test shop: one station (door-1), lids with 30 bags, three codes, one staff."""
    empty_store.put_shop(Shop(tenant_id=TENANT, name="Test Café", settings=NO_BADGES))
    empty_store.put_station(
        Station(api_key_id=DOOR_KEY, tenant_id=TENANT, station_id="door-1", name="Back door")
    )
    empty_store.put_item(TENANT, Item("lid12", "12 oz lids", "bag", stock=30))
    for code in (LID_BAG, LID_BOX, LID_RESTOCK_BOX):
        empty_store.put_code(TENANT, code)
    empty_store.put_staff(TENANT, ANNA)
    return empty_store


def _create_table(
    client,
    name: str,
    *,
    with_sort_key: bool = True,
    extra_attributes: list[str] = (),
    **extra,
) -> None:
    key_schema = [{"AttributeName": keys.PK, "KeyType": "HASH"}]
    attributes = [keys.PK]
    if with_sort_key:
        key_schema.append({"AttributeName": keys.SK, "KeyType": "RANGE"})
        attributes.append(keys.SK)
    attributes += list(extra_attributes)
    client.create_table(
        TableName=name,
        KeySchema=key_schema,
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attributes],
        BillingMode="PAY_PER_REQUEST",
        **extra,
    )

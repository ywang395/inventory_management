"""The alerts Lambda, fed stream records like the ones DynamoDB sends (fake DynamoDB, no email)."""

from dataclasses import replace

from boto3.dynamodb.types import TypeSerializer

from conftest import TENANT
from core.models import Item
from handlers.alerts import email_subject, handle
from store import keys
from store.dynamo import DynamoStore

LIDS = Item("lid12", "12 oz lids", "bag", stock=12, reorder_point=10)
_serializer = TypeSerializer()


def image(item: Item) -> dict:
    """An Items stream image: the stored record in DynamoDB's typed JSON."""
    record = {
        **keys.item_key(TENANT, item.item_id),
        "item_id": item.item_id,
        "name": item.name,
        "smallest_unit": item.smallest_unit,
        "stock": item.stock,
        "needs_count": item.needs_count,
        "reorder_point": item.reorder_point,
    }
    return {name: _serializer.serialize(value) for name, value in record.items()}


def change(before: int, after: int, *, seq: str = "1", event: str = "MODIFY") -> dict:
    return {
        "eventID": f"event-{seq}",
        "eventName": event,
        "dynamodb": {
            "SequenceNumber": seq,
            "OldImage": image(replace(LIDS, stock=before)),
            "NewImage": image(replace(LIDS, stock=after)),
        },
    }


class FakeSns:
    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail_on = fail_on  # the n-th email (1-based) raises, like an SNS outage

    def __call__(self, tenant_id: str, subject: str, text: str) -> None:
        if self.fail_on == len(self.sent) + 1:
            raise RuntimeError("SNS is down")
        self.sent.append((tenant_id, subject, text))


def test_drop_to_reorder_point_emails_the_owner(store: DynamoStore) -> None:
    sns = FakeSns()

    result = handle({"Records": [change(11, 10)]}, store, sns)

    assert result == {"batchItemFailures": []}
    assert sns.sent == [
        (
            TENANT,
            "[Test Cafe] Low stock: 12 oz lids",
            "12 oz lids is low: 10 bags left (reorder at 10).\n\nShop: Test Café",
        )
    ]


def test_changes_that_dont_get_worse_send_nothing(store: DynamoStore) -> None:
    sns = FakeSns()

    handle({"Records": [change(12, 11), change(9, 12), change(12, 10, event="INSERT")]}, store, sns)

    assert sns.sent == []


def test_a_failure_stops_the_batch_and_reports_that_record(store: DynamoStore) -> None:
    sns = FakeSns(fail_on=2)
    records = [change(11, 10, seq="1"), change(1, 0, seq="2"), change(11, 10, seq="3")]

    result = handle({"Records": records}, store, sns)

    # Lambda retries from record 2; record 3 isn't touched now, so it won't email twice.
    assert result == {"batchItemFailures": [{"itemIdentifier": "2"}]}
    assert len(sns.sent) == 1


def test_email_subject_is_plain_ascii_and_short() -> None:
    from core.alerts import AlertKind, StockAlert

    alert = StockAlert(AlertKind.LOW, "Low stock: Crème brûlée " + "x" * 200, "")

    subject = email_subject("Test Café", alert)

    assert subject.startswith("[Test Cafe] Low stock: Creme brulee")
    assert subject.isascii() and len(subject) < 100

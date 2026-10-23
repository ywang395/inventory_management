"""Items table stream -> email the owner when stock gets low, runs out or needs a count.

Every change to an item arrives here with the item before and after (the stream's
OldImage and NewImage). core.alerts decides whether it deserves an email; if so it goes
to an SNS topic, which emails everyone subscribed to it.

Scans never wait for this: they finish as soon as DynamoDB has saved them, and the
stream delivers the change here a moment later. If emailing fails, scans keep working.
"""

import logging
import os
import unicodedata
from collections.abc import Callable

import boto3

from core.alerts import StockAlert, stock_alert
from store.dynamo import DynamoStore, item_from_stream_image

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# publish(tenant_id, subject, text): sends one email through SNS (replaced in tests).
Publish = Callable[[str, str, str], None]

_store: DynamoStore | None = None
_sns = None


def handler(event: dict, context: object) -> dict:
    global _store, _sns
    if _store is None:
        _store = DynamoStore.from_env()  # once per Lambda container, then reused
        _sns = boto3.client("sns")
    return handle(event, _store, _publish)


def handle(event: dict, store: DynamoStore, publish: Publish) -> dict:
    """Process the batch in order. Returns the records Lambda should retry.

    On the first failure we stop and report that record: Lambda retries from it, so
    records after it are handled then (handling them now would email them twice).
    """
    for record in event.get("Records", []):
        try:
            _process(record, store, publish)
        except Exception:
            logger.exception("Alert failed for record %s", record.get("eventID"))
            return {"batchItemFailures": [{"itemIdentifier": _sequence_number(record)}]}
    return {"batchItemFailures": []}


def _process(record: dict, store: DynamoStore, publish: Publish) -> None:
    if record.get("eventName") != "MODIFY":
        return  # new or deleted items (the event source also filters these out)
    change = record["dynamodb"]
    tenant_id, before = item_from_stream_image(change["OldImage"])
    _, after = item_from_stream_image(change["NewImage"])

    alert = stock_alert(before, after)
    if alert is None:
        return
    shop = store.get_shop(tenant_id)
    shop_name = shop.name if shop else tenant_id
    publish(tenant_id, email_subject(shop_name, alert), f"{alert.text}\n\nShop: {shop_name}")
    logger.info("alert tenant=%s item=%s kind=%s", tenant_id, after.item_id, alert.kind.value)


def email_subject(shop_name: str, alert: StockAlert) -> str:
    """SNS email subjects must be plain ASCII and under 100 characters ("Café" -> "Cafe")."""
    subject = f"[{shop_name}] {alert.subject}"
    ascii_only = unicodedata.normalize("NFKD", subject).encode("ascii", "ignore").decode()
    return " ".join(ascii_only.split())[:99]


def _publish(tenant_id: str, subject: str, text: str) -> None:
    assert _sns is not None
    _sns.publish(
        TopicArn=os.environ["ALERTS_TOPIC_ARN"],
        Subject=subject,
        Message=text,
        # Lets each shop owner's email subscription filter to their own shop later
        # (one topic for all shops, a filter policy per subscription).
        MessageAttributes={"tenant_id": {"DataType": "String", "StringValue": tenant_id}},
    )


def _sequence_number(record: dict) -> str:
    return record.get("dynamodb", {}).get("SequenceNumber", "")

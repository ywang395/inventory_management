"""Reads and writes for the inventory tables in DynamoDB.

Table names come from environment variables set by the CDK stack, so nothing is
hard-coded. Reads that feed a stock change are strongly consistent, so new stock is
never computed from a stale value. Key formats live in store/keys.py.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import boto3
from boto3.dynamodb.conditions import Key

from core.models import (
    Code,
    CodeAction,
    Item,
    LogAction,
    LogEntry,
    ScanOutcome,
    ShopSettings,
    Staff,
)
from core.scan import format_utc
from store import keys


@dataclass(frozen=True)
class TableNames:
    shops: str
    items: str
    codes: str
    staff: str
    stations: str
    scan_log: str

    @classmethod
    def from_env(cls) -> "TableNames":
        return cls(
            shops=os.environ["SHOPS_TABLE"],
            items=os.environ["ITEMS_TABLE"],
            codes=os.environ["CODES_TABLE"],
            staff=os.environ["STAFF_TABLE"],
            stations=os.environ["STATIONS_TABLE"],
            scan_log=os.environ["SCAN_LOG_TABLE"],
        )


@dataclass(frozen=True)
class Shop:
    tenant_id: str
    name: str
    settings: ShopSettings


@dataclass(frozen=True)
class Station:
    api_key_id: str
    tenant_id: str
    station_id: str
    name: str


class SaveResult(StrEnum):
    SAVED = "saved"
    DUPLICATE = "duplicate"  # this scan's log entry already exists (offline resend)
    STOCK_CHANGED = "stock_changed"  # another scan changed the stock since we read it


class DynamoStore:
    def __init__(self, tables: TableNames, dynamodb=None) -> None:
        self.tables = tables
        # The resource's client converts plain Python values to DynamoDB types and back,
        # and accepts Key()/Attr() condition builders.
        self.client = (dynamodb or boto3.resource("dynamodb")).meta.client

    @classmethod
    def from_env(cls) -> "DynamoStore":
        return cls(TableNames.from_env())

    # --- Reads --------------------------------------------------------------------------

    def get_station(self, api_key_id: str) -> Station | None:
        record = self._get(self.tables.stations, keys.station_key(api_key_id))
        if record is None:
            return None
        return Station(
            api_key_id=api_key_id,
            tenant_id=record["tenant_id"],
            station_id=record["station_id"],
            name=record.get("name", record["station_id"]),
        )

    def get_shop(self, tenant_id: str) -> Shop | None:
        record = self._get(self.tables.shops, keys.shop_key(tenant_id))
        if record is None:
            return None
        settings = ShopSettings(
            timezone=record["timezone"],
            require_staff_badge=bool(record.get("require_staff_badge", False)),
            undo_own_scans_only=bool(record.get("undo_own_scans_only", False)),
        )
        return Shop(tenant_id=tenant_id, name=record["name"], settings=settings)

    def get_code(self, tenant_id: str, code: str) -> Code | None:
        record = self._get(self.tables.codes, keys.code_key(tenant_id, code))
        return None if record is None else _code(record)

    def list_codes(self, tenant_id: str) -> list[Code]:
        """All of a shop's codes (for the door scan sheet, later the dashboard)."""
        paginator = self.client.get_paginator("query")
        pages = paginator.paginate(
            TableName=self.tables.codes,
            KeyConditionExpression=Key(keys.PK).eq(keys.tenant_pk(tenant_id)),
        )
        return [_code(record) for page in pages for record in page["Items"]]

    def get_item(self, tenant_id: str, item_id: str) -> Item | None:
        # Consistent read: the stock we read is the stock the transaction checks against.
        record = self._get(self.tables.items, keys.item_key(tenant_id, item_id), consistent=True)
        return None if record is None else _item(record)

    def list_items(self, tenant_id: str) -> list[Item]:
        """All of a shop's items, for overviews (stock board, later the dashboard)."""
        paginator = self.client.get_paginator("query")
        pages = paginator.paginate(
            TableName=self.tables.items,
            KeyConditionExpression=Key(keys.PK).eq(keys.tenant_pk(tenant_id)),
        )
        return [_item(record) for page in pages for record in page["Items"]]

    def get_staff(self, tenant_id: str, staff_code: str) -> Staff | None:
        record = self._get(self.tables.staff, keys.staff_key(tenant_id, staff_code))
        if record is None:
            return None
        return Staff(
            staff_code=record["staff_code"],
            name=record["name"],
            active=bool(record.get("active", True)),
        )

    def log_entry_exists(self, tenant_id: str, station: str, scanned_at: datetime) -> bool:
        key = keys.scan_log_key(tenant_id, station, scanned_at)
        return self._get(self.tables.scan_log, key, consistent=True) is not None

    def station_log(
        self, tenant_id: str, station: str, start: datetime, end: datetime
    ) -> list[LogEntry]:
        """This station's log entries from `start` to `end` (both included), oldest first."""
        paginator = self.client.get_paginator("query")
        pages = paginator.paginate(
            TableName=self.tables.scan_log,
            KeyConditionExpression=Key(keys.PK).eq(keys.scan_log_pk(tenant_id, station))
            & Key(keys.SK).between(format_utc(start), format_utc(end)),
            ConsistentRead=True,
        )
        return [_log_entry(record) for page in pages for record in page["Items"]]

    # --- Saving a scan ------------------------------------------------------------------

    def save(self, tenant_id: str, outcome: ScanOutcome, *, expected_stock: int) -> SaveResult:
        """Write the log entry and the item's new stock together: both or neither.

        - The log entry is only written if its key (station + scanned_at) is new, so a
          scan resent by the offline queue is never counted twice.
        - The stock is only updated if it still equals `expected_stock` (what we read).
          If another station changed it in between, nothing is saved and the caller
          reads again and retries.
        """
        entry, item = outcome.entry, outcome.item
        if entry is None or item is None:
            raise ValueError("Only an applied outcome can be saved")

        # Conditions are written as text here: boto3's Attr() builders don't work inside
        # transact_write_items (it puts their placeholders at the wrong level).
        transaction = [
            {
                "Put": {
                    "TableName": self.tables.scan_log,
                    "Item": _log_record(tenant_id, entry),
                    "ConditionExpression": "attribute_not_exists(#pk)",
                    "ExpressionAttributeNames": {"#pk": keys.PK},
                }
            },
            {
                "Update": {
                    "TableName": self.tables.items,
                    "Key": keys.item_key(tenant_id, item.item_id),
                    "UpdateExpression": "SET #stock = :stock, #needs_count = :needs_count",
                    "ConditionExpression": "#stock = :expected_stock",
                    "ExpressionAttributeNames": {
                        "#stock": "stock",
                        "#needs_count": "needs_count",
                    },
                    "ExpressionAttributeValues": {
                        ":stock": item.stock,
                        ":needs_count": item.needs_count,
                        ":expected_stock": expected_stock,
                    },
                }
            },
        ]
        try:
            self.client.transact_write_items(TransactItems=transaction)
        except self.client.exceptions.TransactionCanceledException as error:
            # One reason per action, in order: [log entry, item].
            reasons = [r.get("Code") for r in error.response.get("CancellationReasons", [])]
            if reasons[:1] == ["ConditionalCheckFailed"]:
                return SaveResult.DUPLICATE
            if reasons[1:2] == ["ConditionalCheckFailed"]:
                return SaveResult.STOCK_CHANGED
            raise
        return SaveResult.SAVED

    # --- Writes for setup (seed script, later the dashboard); the Lambdas can't do these.

    def put_shop(self, shop: Shop) -> None:
        self.client.put_item(
            TableName=self.tables.shops,
            Item={
                **keys.shop_key(shop.tenant_id),
                "tenant_id": shop.tenant_id,
                "name": shop.name,
                "timezone": shop.settings.timezone,
                "require_staff_badge": shop.settings.require_staff_badge,
                "undo_own_scans_only": shop.settings.undo_own_scans_only,
            },
        )

    def put_station(self, station: Station) -> None:
        self.client.put_item(
            TableName=self.tables.stations,
            Item={
                **keys.station_key(station.api_key_id),
                "tenant_id": station.tenant_id,
                "station_id": station.station_id,
                "name": station.name,
            },
        )

    def put_item(self, tenant_id: str, item: Item) -> None:
        self.client.put_item(
            TableName=self.tables.items,
            Item={
                **keys.item_key(tenant_id, item.item_id),
                "item_id": item.item_id,
                "name": item.name,
                "smallest_unit": item.smallest_unit,
                "stock": item.stock,
                "needs_count": item.needs_count,
                "category": item.category,
                "reorder_point": item.reorder_point,
                "lead_time_days": item.lead_time_days,
            },
        )

    def put_code(self, tenant_id: str, code: Code) -> None:
        self.client.put_item(
            TableName=self.tables.codes,
            Item={
                **keys.code_key(tenant_id, code.code),
                "code": code.code,
                "item_id": code.item_id,
                "quantity": code.quantity,
                "action": code.action.value,
            },
        )

    def put_staff(self, tenant_id: str, staff: Staff) -> None:
        self.client.put_item(
            TableName=self.tables.staff,
            Item={
                **keys.staff_key(tenant_id, staff.staff_code),
                "staff_code": staff.staff_code,
                "name": staff.name,
                "active": staff.active,
            },
        )

    def _get(self, table: str, key: keys.Key, *, consistent: bool = False) -> dict | None:
        response = self.client.get_item(TableName=table, Key=key, ConsistentRead=consistent)
        return response.get("Item")


def _code(record: dict) -> Code:
    return Code(
        code=record["code"],
        item_id=record["item_id"],
        quantity=int(record["quantity"]),
        action=CodeAction(record["action"]),
    )


def _item(record: dict) -> Item:
    return Item(
        item_id=record["item_id"],
        name=record["name"],
        smallest_unit=record["smallest_unit"],
        stock=int(record["stock"]),
        needs_count=bool(record.get("needs_count", False)),
        category=record.get("category", ""),
        reorder_point=int(record.get("reorder_point", 0)),
        lead_time_days=int(record.get("lead_time_days", 0)),
    )


def _log_record(tenant_id: str, entry: LogEntry) -> dict:
    record = {
        **keys.scan_log_key(tenant_id, entry.station, entry.timestamp),
        keys.ITEM_PK: keys.scan_log_item_pk(tenant_id, entry.item_id),
        "entry_id": entry.entry_id,
        "item_id": entry.item_id,
        "action": entry.action.value,
        "code": entry.code,
        "station": entry.station,
        "change": entry.change,
        "shortfall": entry.shortfall,
    }
    # Optional fields are left out rather than stored empty.
    if entry.staff:
        record["staff"] = entry.staff
    if entry.undoes:
        record["undoes"] = entry.undoes
    return record


def _log_entry(record: dict) -> LogEntry:
    return LogEntry(
        entry_id=record["entry_id"],
        item_id=record["item_id"],
        timestamp=datetime.fromisoformat(record[keys.SK]),
        action=LogAction(record["action"]),
        code=record["code"],
        staff=record.get("staff"),
        station=record["station"],
        change=int(record["change"]),
        shortfall=int(record.get("shortfall", 0)),
        undoes=record.get("undoes"),
    )

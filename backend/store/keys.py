"""Every DynamoDB key in one place, so handlers and scripts can't disagree on formats.

Each shop (tenant) owns everything under `TENANT#<tenant_id>`. The tenant always
comes from the station's API key or the owner's login, never from a request body.
Table layout (defined in infra/stacks/inventory_stack.py):

    Shops     pk = TENANT#cafe-01
    Items     pk = TENANT#cafe-01                  sk = ITEM#lids_12oz
    Codes     pk = TENANT#cafe-01                  sk = CODE#INV-LID12-1BOX
    Staff     pk = TENANT#cafe-01                  sk = STAFF#STAFF-001
    Stations  pk = APIKEY#<api key id>             (looked up to find the tenant)
    ScanLog   pk = TENANT#cafe-01#STATION#door-1   sk = <scanned_at in UTC>
              index by_item: item_pk = TENANT#cafe-01#ITEM#lids_12oz, sk = <scanned_at>
"""

from datetime import datetime

from core.scan import format_utc

# Attribute and index names, shared with the CDK stack (a test checks they match).
PK = "pk"
SK = "sk"
ITEM_PK = "item_pk"
BY_ITEM_INDEX = "by_item"

Key = dict[str, str]


def _part(name: str, value: str) -> str:
    """A key part must be non-empty and can't contain '#', which separates parts.

    Otherwise an id like "lids#x" could make one key look like another.
    """
    if not value:
        raise ValueError(f"{name} is required")
    if "#" in value:
        raise ValueError(f"{name} can't contain '#': {value!r}")
    return value


def tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{_part('tenant_id', tenant_id)}"


def shop_key(tenant_id: str) -> Key:
    return {PK: tenant_pk(tenant_id)}


def item_key(tenant_id: str, item_id: str) -> Key:
    return {PK: tenant_pk(tenant_id), SK: f"ITEM#{_part('item_id', item_id)}"}


def code_key(tenant_id: str, code: str) -> Key:
    return {PK: tenant_pk(tenant_id), SK: f"CODE#{_part('code', code)}"}


def staff_key(tenant_id: str, staff_code: str) -> Key:
    return {PK: tenant_pk(tenant_id), SK: f"STAFF#{_part('staff_code', staff_code)}"}


def station_key(api_key_id: str) -> Key:
    return {PK: f"APIKEY#{_part('api_key_id', api_key_id)}"}


def scan_log_pk(tenant_id: str, station: str) -> str:
    return f"{tenant_pk(tenant_id)}#STATION#{_part('station', station)}"


def scan_log_key(tenant_id: str, station: str, scanned_at: datetime) -> Key:
    """Station + scanned_at is also the duplicate check: a resent scan has the same key."""
    return {PK: scan_log_pk(tenant_id, station), SK: format_utc(scanned_at)}


def scan_log_item_pk(tenant_id: str, item_id: str) -> str:
    """Partition key of the by_item index (an item's history across stations)."""
    return f"{tenant_pk(tenant_id)}#ITEM#{_part('item_id', item_id)}"

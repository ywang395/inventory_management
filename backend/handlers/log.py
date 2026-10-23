"""GET /log?item=<item_id>[&limit=50] (owner API): an item's recent activity, newest first."""

from core.models import LogEntry
from core.scan import format_utc
from handlers.http import NO_SHOP, owner_response, owner_tenant
from handlers.items import item_body
from store.dynamo import DynamoStore

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_store: DynamoStore | None = None


def handler(event: dict, context: object) -> dict:
    global _store
    if _store is None:
        _store = DynamoStore.from_env()  # once per Lambda container, then reused
    return handle(event, _store)


def handle(event: dict, store: DynamoStore) -> dict:
    tenant = owner_tenant(event)
    if tenant is None:
        return owner_response(event, 403, NO_SHOP)

    params = event.get("queryStringParameters") or {}
    item_id = (params.get("item") or "").strip()
    raw_limit = params.get("limit") or str(DEFAULT_LIMIT)
    try:
        if not item_id:
            raise ValueError("item is required.")
        if not raw_limit.isdigit():
            raise ValueError("limit must be a whole number.")
        item = store.get_item(tenant, item_id)  # also rejects ids that can't be a key
    except ValueError as error:
        return owner_response(event, 400, {"status": "error", "message": str(error)})
    limit = min(max(int(raw_limit), 1), MAX_LIMIT)
    if item is None:
        return owner_response(event, 404, {"status": "error", "message": "Unknown item."})

    entries = store.item_log(tenant, item_id, limit)
    # An undo is always newer than the scan it reverses, so within this newest-first
    # window every undone scan's undo is here too.
    undone = {entry.undoes for entry in entries if entry.undoes}
    return owner_response(
        event,
        200,
        {
            "item": item_body(item),
            "entries": [entry_body(e, e.entry_id in undone) for e in entries],
        },
    )


def entry_body(entry: LogEntry, undone: bool) -> dict:
    return {
        "entry_id": entry.entry_id,
        "scanned_at": format_utc(entry.timestamp),
        "action": entry.action.value,
        "code": entry.code,
        "change": entry.change,
        "shortfall": entry.shortfall,
        "staff": entry.staff,
        "station": entry.station,
        "undone": undone,
    }

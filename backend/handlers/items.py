"""GET /items (owner API): every item of the owner's shop, with its stock and status."""

from core.models import Item
from core.stock import stock_status
from handlers.http import NO_SHOP, owner_response, owner_tenant
from store.dynamo import DynamoStore

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
    shop = store.get_shop(tenant)
    items = sorted(store.list_items(tenant), key=lambda item: (item.category, item.name))
    return owner_response(
        event,
        200,
        {
            "shop": {
                "name": shop.name if shop else tenant,
                "timezone": shop.settings.timezone if shop else "UTC",
            },
            "items": [item_body(item) for item in items],
        },
    )


def item_body(item: Item) -> dict:
    """An item as the dashboard sees it. `status` (ok / low / out / needs_count) comes
    from core, so the dashboard and the alerts always agree on what "low" means."""
    return {
        "item_id": item.item_id,
        "name": item.name,
        "category": item.category,
        "smallest_unit": item.smallest_unit,
        "stock": item.stock,
        "reorder_point": item.reorder_point,
        "needs_count": item.needs_count,
        "status": stock_status(item).value,
    }

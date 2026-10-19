"""GET /station/config: the shop settings a station needs (it caches them for offline use)."""

from handlers.http import NOT_REGISTERED, find_station, response
from store.dynamo import DynamoStore

_store: DynamoStore | None = None


def handler(event: dict, context: object) -> dict:
    global _store
    if _store is None:
        _store = DynamoStore.from_env()  # once per Lambda container, then reused
    return handle(event, _store)


def handle(event: dict, store: DynamoStore) -> dict:
    station = find_station(event, store)
    if station is None:
        return NOT_REGISTERED
    shop = store.get_shop(station.tenant_id)
    if shop is None:
        return response(500, {"status": "error", "message": "Shop is not set up."})
    return response(
        200,
        {
            "shop_name": shop.name,
            "station_id": station.station_id,
            "station_name": station.name,
            "timezone": shop.settings.timezone,
            "require_staff_badge": shop.settings.require_staff_badge,
            "undo_own_scans_only": shop.settings.undo_own_scans_only,
        },
    )

"""POST /scan: one scan (or undo) from a station.

Flow: API key -> station and shop -> load what the scan needs -> core logic ->
save the log entry and new stock in one transaction. If another scan changed the
stock in between, read again and retry.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from core.codes import CodeKind, classify_code, normalize_code
from core.models import OutcomeStatus, ScanAction, ScanOutcome, ScanRequest
from core.scan import build_undo, find_undo_target, process_scan
from core.shop import shop_day_bounds
from handlers.http import NOT_REGISTERED, find_station, response
from store.dynamo import DynamoStore, SaveResult, Shop

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 3
# A station clock a little ahead is fine; far ahead would break "today" for undo.
MAX_CLOCK_AHEAD = timedelta(minutes=5)

_store: DynamoStore | None = None


def handler(event: dict, context: object) -> dict:
    global _store
    if _store is None:
        _store = DynamoStore.from_env()  # once per Lambda container, then reused
    return handle(event, _store)


class BadRequest(Exception):
    pass


def handle(event: dict, store: DynamoStore, *, now: datetime | None = None) -> dict:
    station = find_station(event, store)
    if station is None:
        return NOT_REGISTERED
    shop = store.get_shop(station.tenant_id)
    if shop is None:
        logger.error("No shop record for tenant %s", station.tenant_id)
        return response(500, {"status": "error", "message": "Shop is not set up."})

    try:
        request = parse_request(event.get("body"), station.station_id, now or datetime.now(UTC))
    except BadRequest as error:
        return response(400, {"status": "error", "message": str(error)})

    for _ in range(MAX_ATTEMPTS):
        reply = _attempt(request, shop, store)
        if reply is not None:
            return reply
    return response(503, {"status": "busy", "message": "Busy, please scan again."})


def parse_request(body: str | None, station_id: str, now: datetime) -> ScanRequest:
    """Check the JSON body and turn it into a ScanRequest. Any `station` in it is ignored."""
    try:
        data = json.loads(body or "")
    except json.JSONDecodeError as error:
        raise BadRequest("Body must be JSON.") from error
    if not isinstance(data, dict):
        raise BadRequest("Body must be a JSON object.")

    code = data.get("code")
    if not isinstance(code, str) or not code.strip():
        raise BadRequest("code is required.")
    try:
        action = ScanAction(data.get("action", "scan"))
    except ValueError as error:
        raise BadRequest('action must be "scan" or "undo".') from error

    staff = data.get("staff")
    if staff is not None and not isinstance(staff, str):
        raise BadRequest("staff must be text.")
    staff = normalize_code(staff) if staff and staff.strip() else None

    raw_time = data.get("scanned_at")
    if not isinstance(raw_time, str):
        raise BadRequest("scanned_at is required.")
    try:
        scanned_at = datetime.fromisoformat(raw_time)
    except ValueError as error:
        raise BadRequest("scanned_at must be an ISO 8601 time.") from error
    if scanned_at.tzinfo is None:
        raise BadRequest("scanned_at must include a time zone.")
    if scanned_at > now + MAX_CLOCK_AHEAD:
        raise BadRequest("scanned_at is in the future: check the station clock.")

    return ScanRequest(
        code=normalize_code(code),
        action=action,
        staff=staff,
        station=station_id,
        scanned_at=scanned_at,
    )


def _attempt(request: ScanRequest, shop: Shop, store: DynamoStore) -> dict | None:
    """One try at applying the request. Returns the reply, or None to retry."""
    tenant = shop.tenant_id
    settings = shop.settings
    already_seen = store.log_entry_exists(tenant, request.station, request.scanned_at)
    staff = None
    if settings.require_staff_badge and request.staff:
        staff = store.get_staff(tenant, request.staff)

    if request.action is ScanAction.UNDO:
        start, _ = shop_day_bounds(request.scanned_at, settings)
        todays_log = store.station_log(tenant, request.station, start, request.scanned_at)
        target = find_undo_target(request, todays_log, settings)
        item = store.get_item(tenant, target.item_id) if target else None
        if target and item is None:
            return _missing_item(target.item_id)
        outcome = build_undo(
            request, target, item, settings=settings, staff=staff, already_seen=already_seen
        )
    else:
        if classify_code(request.code) not in (CodeKind.INVENTORY, CodeKind.UPC):
            return _ignored("Not an inventory code.")
        code = store.get_code(tenant, request.code)
        if code is None:
            return _ignored(f"Unknown code {request.code}.")
        item = store.get_item(tenant, code.item_id)
        if item is None:
            return _missing_item(code.item_id)
        outcome = process_scan(
            request, code, item, settings=settings, staff=staff, already_seen=already_seen
        )

    if outcome.status is not OutcomeStatus.APPLIED:
        return _reply(request, outcome)

    assert item is not None  # an applied outcome always has an item
    result = store.save(tenant, outcome, expected_stock=item.stock)
    if result is SaveResult.STOCK_CHANGED:
        return None
    if result is SaveResult.DUPLICATE:
        return _reply(request, ScanOutcome(OutcomeStatus.DUPLICATE))
    return _reply(request, outcome)


def _reply(request: ScanRequest, outcome: ScanOutcome) -> dict:
    body: dict = {"status": outcome.status.value, "message": _message(outcome)}
    if outcome.item is not None and outcome.entry is not None:
        body["item"] = {
            "item_id": outcome.item.item_id,
            "name": outcome.item.name,
            "stock": outcome.item.stock,
            "needs_count": outcome.item.needs_count,
        }
        body["change"] = outcome.entry.change
    logger.info(
        "scan station=%s code=%s action=%s status=%s",
        request.station,
        request.code,
        request.action.value,
        outcome.status.value,
    )
    return response(200, body)


def _message(outcome: ScanOutcome) -> str:
    """A short line for the station screen."""
    if outcome.status is OutcomeStatus.DUPLICATE:
        return "Already recorded."
    if outcome.status is not OutcomeStatus.APPLIED:
        return outcome.reason
    item, entry = outcome.item, outcome.entry
    assert item is not None and entry is not None
    prefix = "Undone: " if entry.undoes else ""
    message = f"{prefix}{item.name} {entry.change:+d} {item.smallest_unit}, {item.stock} left."
    if entry.shortfall:
        message += f" {entry.shortfall} more than in stock: please count {item.name}."
    return message


def _ignored(message: str) -> dict:
    return response(200, {"status": "ignored", "message": message})


def _missing_item(item_id: str) -> dict:
    logger.error("Code points to missing item %s", item_id)
    return response(500, {"status": "error", "message": f"Item {item_id} is missing."})

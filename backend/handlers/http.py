"""Small helpers shared by the API Gateway handlers."""

import json

from store.dynamo import DynamoStore, Station


def response(status_code: int, body: dict) -> dict:
    """The shape API Gateway (Lambda proxy integration) expects back."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def find_station(event: dict, store: DynamoStore) -> Station | None:
    """The station that owns the request's API key, or None if it isn't registered.

    API Gateway has already checked the key is valid and passes its id here. The
    station (and so the shop) always comes from this, never from the request body.
    """
    api_key_id = event.get("requestContext", {}).get("identity", {}).get("apiKeyId")
    if not api_key_id:
        return None
    return store.get_station(api_key_id)


NOT_REGISTERED = response(403, {"status": "error", "message": "This station is not registered."})

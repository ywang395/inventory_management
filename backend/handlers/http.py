"""Small helpers shared by the API Gateway handlers."""

import json
import os

from store.dynamo import DynamoStore, Station


def response(status_code: int, body: dict, headers: dict[str, str] | None = None) -> dict:
    """The shape API Gateway (Lambda proxy integration) expects back."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", **(headers or {})},
        "body": json.dumps(body),
    }


# --- Owner API (dashboard, Cognito login) -----------------------------------------------


def owner_tenant(event: dict) -> str | None:
    """The signed-in owner's shop: the `custom:tenant_id` claim of their ID token.

    API Gateway's Cognito authorizer has already checked the token and passes its claims
    here. The shop always comes from the login, never from the request.
    """
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims", {})
    return claims.get("custom:tenant_id") or None


def owner_response(event: dict, status_code: int, body: dict) -> dict:
    """A reply the dashboard's browser is allowed to read (CORS).

    The dashboard runs on another address than the API, so the browser only hands it
    the reply if it carries Access-Control-Allow-Origin for that address. Only the
    dashboard addresses in DASHBOARD_ORIGINS get it.
    """
    headers = {name.lower(): value for name, value in (event.get("headers") or {}).items()}
    origin = headers.get("origin")
    allowed = os.environ.get("DASHBOARD_ORIGINS", "").split(",")
    cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin in allowed else {}
    return response(status_code, body, cors)


NO_SHOP = {"status": "error", "message": "This login isn't linked to a shop."}


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

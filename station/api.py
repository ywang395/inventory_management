"""Talks to the station API (GET /station/config, POST /scan) with Python's own urllib.

Every answer falls into one of three cases:
- decided: the API has dealt with the scan (applied, duplicate, nothing to undo,
  rejected, ignored, or a bad request that would fail again). Remove it from the outbox.
- NotRegistered (403): wrong API key or the station isn't set up. Retrying won't help.
- Unreachable: no internet, a timeout, "busy" (503), too many requests (429) or a
  server error. Keep the scan and try again later.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn

from logic import Outgoing
from outbox import Outbox


class NotRegistered(Exception):
    """The API key is wrong or not linked to a station."""


class Unreachable(Exception):
    """No usable answer right now: keep the scan and try again later."""


@dataclass(frozen=True)
class Api:
    base_url: str  # the stack's ApiUrl output, e.g. https://....amazonaws.com/dev/
    key: str
    timeout_seconds: float = 5.0

    def get_config(self) -> dict:
        status, body = self._call("GET", "station/config")
        if status == 200:
            return body
        _raise(status, body)

    def send_scan(self, scan: Outgoing) -> str:
        """Send one scan and return the line to show. Once this returns, the API has decided."""
        status, body = self._call("POST", "scan", scan.body())
        message = body.get("message", "")
        if status == 200:
            return message
        if status == 400:
            return f"Not saved: {message}"  # sending it again would fail the same way
        _raise(status, body)

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/{path}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={"x-api-key": self.key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status, _json(response.read())
        except urllib.error.HTTPError as error:  # 4xx / 5xx: still an answer, with a body
            return error.code, _json(error.read())
        except OSError as error:  # no connection, DNS failure, timeout (URLError is an OSError)
            raise Unreachable(str(error)) from error


def _raise(status: int, body: dict) -> NoReturn:
    message = body.get("message", "")
    if status == 403:
        raise NotRegistered(message or "Forbidden")
    raise Unreachable(f"HTTP {status} {message}".strip())


def _json(raw: bytes) -> dict:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def flush(outbox: Outbox, send: Callable[[Outgoing], str]) -> list[str]:
    """Send waiting scans, oldest first, and return the API's messages.

    Stops at the first scan that can't be sent now; it and everything after it stay
    waiting, in order, so scans always reach the API in the order they were made.
    """
    messages = []
    for row_id, scan in outbox.pending():
        try:
            messages.append(send(scan))
        except Unreachable:
            break
        outbox.remove(row_id)
    return messages

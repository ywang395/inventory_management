"""The station's HTTP calls, against a tiny local server that answers like the real API."""

import json
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from api import Api, NotRegistered, Unreachable, flush
from logic import Outgoing
from outbox import Outbox

T0 = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)
SCAN = Outgoing("INV-LID12-1BAG", "scan", T0)


@dataclass
class FakeApi:
    api: Api
    replies: list[tuple[int, dict]] = field(default_factory=list)  # answered in this order
    requests: list[dict] = field(default_factory=list)  # what the station sent


@pytest.fixture
def fake() -> Iterator[FakeApi]:
    fake = FakeApi(api=Api("", ""))

    class Handler(BaseHTTPRequestHandler):
        def answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            fake.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "key": self.headers.get("x-api-key"),
                    "body": json.loads(self.rfile.read(length)) if length else None,
                }
            )
            status, reply = fake.replies.pop(0)
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = answer

        def log_message(self, *args: object) -> None:
            pass  # keep the test output quiet

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake.api = Api(f"http://127.0.0.1:{server.server_port}/dev/", "test-key", timeout_seconds=2)
    yield fake
    server.shutdown()
    server.server_close()


def test_scan_is_posted_with_the_key_and_returns_the_message(fake: FakeApi) -> None:
    fake.replies.append((200, {"status": "applied", "message": "12 oz cup lids -1 bag, 11 left."}))

    message = fake.api.send_scan(SCAN)

    assert message == "12 oz cup lids -1 bag, 11 left."
    assert fake.requests == [
        {"method": "POST", "path": "/dev/scan", "key": "test-key", "body": SCAN.body()}
    ]


def test_bad_request_is_final(fake: FakeApi) -> None:
    fake.replies.append((400, {"status": "error", "message": "scanned_at is in the future."}))

    assert fake.api.send_scan(SCAN) == "Not saved: scanned_at is in the future."


def test_forbidden_means_not_registered(fake: FakeApi) -> None:
    fake.replies.append((403, {"message": "Forbidden"}))

    with pytest.raises(NotRegistered):
        fake.api.send_scan(SCAN)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_busy_or_server_error_means_try_later(fake: FakeApi, status: int) -> None:
    fake.replies.append((status, {"status": "busy", "message": "Busy, please scan again."}))

    with pytest.raises(Unreachable):
        fake.api.send_scan(SCAN)


def test_no_connection_means_try_later() -> None:
    with socket.socket() as sock:  # find a port with nothing listening on it
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    with pytest.raises(Unreachable):
        Api(f"http://127.0.0.1:{port}/dev/", "test-key", timeout_seconds=2).send_scan(SCAN)


def test_station_config(fake: FakeApi) -> None:
    fake.replies.append((200, {"shop_name": "Test Café"}))

    assert fake.api.get_config() == {"shop_name": "Test Café"}
    assert fake.requests[0]["path"] == "/dev/station/config"


def test_flush_stops_at_the_first_scan_that_cant_be_sent() -> None:
    outbox = Outbox(":memory:")
    for code in ("INV-A", "INV-B", "INV-C"):
        outbox.add(Outgoing(code, "scan", T0))
    answers = iter(["A done"])

    def send(scan: Outgoing) -> str:
        try:
            return next(answers)
        except StopIteration:
            raise Unreachable("offline") from None

    assert flush(outbox, send) == ["A done"]
    assert [s.code for _, s in outbox.pending()] == ["INV-B", "INV-C"]  # still in order

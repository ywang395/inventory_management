"""The scanner station on a laptop: type a code (or use a USB scanner) and press Enter.

Set up once (writes station/.env with the API URL and key; the key isn't shown):

    uv run python scripts/station_env.py

Run from the repo root:

    uv run --env-file station/.env python station/scanner_sim.py

A USB barcode scanner works as a keyboard: it types the code and presses Enter for you.
Scans are saved in station/station.db before they're sent, so nothing is lost offline;
waiting scans are sent again every 10 seconds and with every new scan.
"""

import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# Import the backend code (core) the same way the Lambdas do: one set of code rules.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from api import Api, NotRegistered, Unreachable, flush
from logic import Outgoing, StationConfig, StationState, on_scan
from outbox import Outbox

RETRY_SECONDS = 10
DEFAULT_DB = Path(__file__).resolve().parent / "station.db"


class Station:
    """Scans in, lines to show out: the rules, the outbox and the API wired together."""

    def __init__(
        self,
        config: StationConfig,
        outbox: Outbox,
        send: Callable[[Outgoing], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.outbox = outbox
        self.send = send
        self.clock = clock
        self.state = StationState()
        # The scan loop and the retry thread take turns, so a scan is never sent twice
        # at the same time (it would be harmless, the API ignores the copy, but confusing).
        self._lock = threading.Lock()

    def scan(self, raw: str) -> list[str]:
        with self._lock:
            step = on_scan(self.state, raw, self.clock(), self.config)
            self.state = step.state
            lines = [step.message] if step.message else []
            if step.send:
                self.outbox.add(step.send)  # saved first: survives no internet or a restart
                lines += flush(self.outbox, self.send)
                if waiting := self.outbox.count():
                    lines.append(f"No connection: saved, {waiting} waiting.")
            return lines

    def retry(self) -> list[str]:
        """Send what's waiting; returns the API's messages for scans that got through."""
        with self._lock:
            return flush(self.outbox, self.send) if self.outbox.count() else []


def load_config(api: Api, outbox: Outbox) -> StationConfig | None:
    """Fresh settings from the API (and save them), or the saved ones when offline."""
    try:
        body = api.get_config()
    except Unreachable:
        body = outbox.load_config()
        if body is not None:
            print("Offline: using the saved shop settings.")
    else:
        outbox.save_config(body)
    return StationConfig.from_api(body) if body is not None else None


def retry_loop(station: Station, stop: threading.Event) -> None:
    while not stop.wait(RETRY_SECONDS):
        try:
            lines = station.retry()
        except NotRegistered:
            return  # the scan loop reports it on the next scan
        for line in lines:
            print(f"\n  Sent a waiting scan: {line}\n> ", end="", flush=True)


def main() -> int:
    try:
        api = Api(os.environ["API_URL"], os.environ["API_KEY"])
    except KeyError as missing:
        print(
            f"{missing} isn't set. Run `uv run python scripts/station_env.py`, then start with\n"
            "`uv run --env-file station/.env python station/scanner_sim.py`.",
            file=sys.stderr,
        )
        return 1
    outbox = Outbox(os.environ.get("STATION_DB", DEFAULT_DB))

    try:
        config = load_config(api, outbox)
    except NotRegistered as error:
        print(f"This station isn't registered ({error}). Check API_KEY.", file=sys.stderr)
        return 1
    if config is None:
        print("No connection and no saved settings: connect once to set up.", file=sys.stderr)
        return 1

    badges = "required" if config.require_staff_badge else "off"
    print(f"{config.shop_name} · {config.station_name} ({config.station_id})")
    print(f"Staff badges: {badges}. To undo: scan INV-UNDO, then the code (within 10 s).")
    print("Scan a code and press Enter. Control + C to quit.\n")

    station = Station(config, outbox, api.send_scan)
    stop = threading.Event()
    threading.Thread(target=retry_loop, args=(station, stop), daemon=True).start()
    try:
        for line in station.retry():  # scans left over from last time
            print(f"  Sent a waiting scan: {line}")
        while True:
            for line in station.scan(input("> ")):
                print(f"  {line}")
    except NotRegistered as error:
        print(f"  This station isn't registered ({error}). Check API_KEY.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        stop.set()
        if waiting := outbox.count():
            print(
                f"{waiting} scan(s) still waiting: they'll be sent when the station starts again."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

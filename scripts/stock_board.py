"""A live stock board for demos: scan with the phone, watch the numbers change here.

Run from the repo root, after `aws login`:

    uv run python scripts/stock_board.py         # refreshes every 3 s; Ctrl+C to stop
    uv run python scripts/stock_board.py --once  # print once and exit

Read-only: it shows the test shop's items and today's scans at door-1 and never changes
anything. Items in the "Test" category (like the phone-test candle) are left out, since
they aren't shop stock.
"""

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Import the backend code (core, store) the same way the Lambdas do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from core.models import Item, LogEntry
from core.shop import shop_day_bounds
from core.stock import StockStatus, stock_status
from seed import STACK_NAME, STATION_ID, TENANT, stack_outputs, table_names
from store.dynamo import DynamoStore, Shop

REFRESH_SECONDS = 3
RECENT_SCANS = 6
HIDDEN_CATEGORY = "Test"

# Terminal colors (ANSI escape codes): the dashboard's green / yellow / red, in a terminal.
GREEN, YELLOW, RED, BOLD, DIM, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[2m", "\033[0m"
)  # fmt: skip
CLEAR_SCREEN = "\033[2J\033[H"

STATUS_LABELS = {
    StockStatus.OK: ("OK", GREEN),
    StockStatus.LOW: ("LOW - reorder", YELLOW),
    StockStatus.OUT: ("OUT", RED),
    StockStatus.NEEDS_COUNT: ("COUNT NEEDED", RED),
}


def render(
    shop: Shop, items: list[Item], scans: list[LogEntry], now: datetime, *, color: bool = True
) -> str:
    """The board as text: every item's stock and status, then today's latest scans."""

    def paint(text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if color else text

    zone = shop.settings.zone
    by_id = {item.item_id: item for item in items}
    shown = sorted(
        (item for item in items if item.category != HIDDEN_CATEGORY),
        key=lambda item: (item.category, item.name),
    )

    lines = [
        f"{paint(shop.name + ' - stock', BOLD)}   {now.astimezone(zone):%H:%M:%S}",
        "",
        f"  {'Item':<24}{'Stock':<14}Status",
    ]
    for item in shown:
        label, code = STATUS_LABELS[stock_status(item)]
        stock = f"{item.stock} {item.smallest_unit}"
        lines.append(f"  {item.name:<24}{stock:<14}{paint(label, code)}")

    lines += ["", paint(f"Last scans today at {STATION_ID}", BOLD)]
    latest = sorted(scans, key=lambda entry: entry.timestamp, reverse=True)[:RECENT_SCANS]
    if not latest:
        lines.append(paint("  No scans yet today.", DIM))
    for entry in latest:
        item = by_id.get(entry.item_id)
        name = item.name if item else entry.item_id
        change = f"{entry.change:+d} {item.smallest_unit if item else ''}"
        when = entry.timestamp.astimezone(zone)
        lines.append(f"  {when:%H:%M:%S}  {name:<24}{change:<14}{entry.action.value}")
    return "\n".join(lines)


def read_board(store: DynamoStore, shop: Shop) -> str:
    now = datetime.now(UTC)
    start, _ = shop_day_bounds(now, shop.settings)
    scans = store.station_log(TENANT, STATION_ID, start, now)
    return render(shop, store.list_items(TENANT), scans, now)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live stock board for the test shop.")
    parser.add_argument("--once", action="store_true", help="print the board once and exit")
    args = parser.parse_args()

    try:
        outputs = stack_outputs(boto3.client("cloudformation"), STACK_NAME)
        store = DynamoStore(table_names(outputs))
        shop = store.get_shop(TENANT)
        if shop is None:
            print(f"Shop {TENANT!r} not found: run scripts/seed.py first.", file=sys.stderr)
            return 1
        if args.once:
            print(read_board(store, shop))
            return 0
    except (ClientError, BotoCoreError) as error:
        print(f"AWS error: {error}\nIs your `aws login` still valid?", file=sys.stderr)
        return 1

    board = ""
    try:
        while True:
            try:
                board = read_board(store, shop)
            except (ClientError, BotoCoreError) as error:
                # A short network drop mid-demo shouldn't end the board: keep the last
                # numbers on screen, say so, and try again.
                board += f"\n\n{RED}Can't reach AWS ({type(error).__name__}), retrying...{RESET}"
            print(CLEAR_SCREEN + board, flush=True)
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""The demo stock board's text (no AWS: render() only formats what it's given)."""

from datetime import timedelta

from core.models import Item, LogEntry
from core.scan import process_scan
from factories import LID_BAG, LID_BOX, NO_BADGES, T0, lids, scan
from stock_board import render
from store.dynamo import Shop

SHOP = Shop(tenant_id="cafe-01", name="Test Café", settings=NO_BADGES)


def line_with(board: str, text: str) -> str:
    (line,) = [line for line in board.splitlines() if text in line]
    return line


def take(item: Item, code, seconds: int) -> LogEntry:
    entry = process_scan(scan(code.code, seconds=seconds), code, item, settings=NO_BADGES).entry
    assert entry is not None
    return entry


def test_board_shows_each_items_stock_and_status() -> None:
    items = [
        Item("lids", "12 oz cup lids", "bag", stock=10, category="Cups & lids", reorder_point=10),
        Item("milk", "Producers whole milk", "gallon", stock=8, category="Dairy", reorder_point=6),
        Item("cream", "Vanilla ice cream", "tub", stock=0, category="Ice cream"),
    ]

    board = render(SHOP, items, [], T0, color=False)

    assert "10 bag" in line_with(board, "12 oz cup lids")
    assert "LOW - reorder" in line_with(board, "12 oz cup lids")
    assert "8 gallon" in line_with(board, "Producers whole milk")
    assert "OK" in line_with(board, "Producers whole milk")
    assert "OUT" in line_with(board, "Vanilla ice cream")
    assert "No scans yet today." in board


def test_board_leaves_out_test_items() -> None:
    candle = Item("candle", "Rosemary & Thyme candle", "candle", stock=10, category="Test")

    board = render(SHOP, [lids(), candle], [], T0, color=False)

    assert "candle" not in board


def test_board_lists_scans_newest_first_in_shop_time() -> None:
    item = lids(stock=30)
    scans = [take(item, LID_BAG, seconds=60), take(item, LID_BOX, seconds=120)]

    board = render(SHOP, [item], scans, T0 + timedelta(minutes=5), color=False)

    # T0 is 17:00 UTC, which is 10:00 in Los Angeles.
    newest, older = board.split("Last scans today")[1].splitlines()[1:3]
    assert "10:02:00" in newest and "-12 bag" in newest and "take" in newest
    assert "10:01:00" in older and "-1 bag" in older


def test_status_is_colored_for_the_terminal() -> None:
    low = Item("lids", "12 oz cup lids", "bag", stock=10, reorder_point=10)

    board = render(SHOP, [low], [], T0)

    assert "\033[33mLOW - reorder\033[0m" in board  # yellow

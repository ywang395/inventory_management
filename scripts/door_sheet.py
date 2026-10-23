"""Make the door scan sheet: one QR code per INV- code, grouped by item, ready to print.

Run from the repo root, after `aws login`:

    uv run python scripts/door_sheet.py   # writes out/door-sheet.html and opens it

Then print it from the browser (Cmd+P). The codes and items come from the dev tables,
so the sheet always matches what the API knows. Read-only: it never changes anything.

Supplier barcodes (UPC) aren't on the sheet: they're printed on the products already.
`INV-UNDO` isn't either, for now: undo mode belongs to the station, which isn't built yet.
"""

import argparse
import sys
import webbrowser
from html import escape
from pathlib import Path

# Import the backend code (core, store) the same way the Lambdas do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import boto3
import segno
from botocore.exceptions import BotoCoreError, ClientError

from core.codes import CodeKind, classify_code
from core.models import Code, CodeAction, Item
from core.stock import units
from seed import STACK_NAME, TENANT, stack_outputs, table_names
from store.dynamo import DynamoStore

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "out" / "door-sheet.html"
HIDDEN_CATEGORY = "Test"  # test-only items (like the phone-test candle) aren't shop stock

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{shop} - door scan sheet</title>
<style>
  @page {{ size: letter; margin: 0.4in; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; color: #111;
         margin: 0.4in; }}
  @media print {{ body {{ margin: 0; }} }}
  h1 {{ font-size: 20pt; margin: 0; }}
  header p {{ font-size: 11pt; margin: 4pt 0 14pt; }}
  main {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14pt 18pt; }}
  section {{ break-inside: avoid; border-top: 2pt solid #111; padding-top: 4pt; }}
  h2 {{ font-size: 12pt; margin: 0 0 6pt; }}
  .cards {{ display: flex; gap: 8pt; }}
  figure {{ margin: 0; width: 1.15in; text-align: center; }}
  figure svg {{ display: block; width: 1.1in; height: 1.1in; margin: 0 auto; }}
  figcaption strong {{ display: block; font-size: 10pt; }}
  figcaption span {{ font: 7pt ui-monospace, Menlo, monospace; color: #555; }}
  .restock strong {{ color: #0a6b2d; }}
  footer {{ margin-top: 16pt; font-size: 9pt; color: #333; }}
</style>
</head>
<body>
<header>
  <h1>{shop} - storage door</h1>
  <p>Scan what you take. Scan <b>Restock</b> when a delivery comes in.</p>
</header>
<main>
{sections}
</main>
<footer>{footer}</footer>
</body>
</html>
"""


def label(code: Code, item: Item) -> str:
    amount = units(abs(code.quantity), item.smallest_unit)
    return f"Take {amount}" if code.action is CodeAction.TAKE else f"Restock +{amount}"


def qr_code(text: str) -> segno.QRCode:
    # make_qr, not make: for short text segno would pick a Micro QR code, which phone
    # cameras and most scanners can't read.
    return segno.make_qr(text, error="m")


def build_sheet(shop_name: str, items: list[Item], codes: list[Code]) -> str:
    """The printable page (HTML with the QR codes drawn inline as SVG)."""
    shown = {item.item_id: item for item in items if item.category != HIDDEN_CATEGORY}
    sections = []
    for item in sorted(shown.values(), key=lambda item: (item.category, item.name)):
        item_codes = [
            code
            for code in codes
            if code.item_id == item.item_id and classify_code(code.code) is CodeKind.INVENTORY
        ]
        # Takes first, smallest first; restocks after.
        item_codes.sort(key=lambda code: (code.action is CodeAction.RESTOCK, abs(code.quantity)))
        if item_codes:
            cards = "".join(_card(code, item) for code in item_codes)
            sections.append(
                f'<section><h2>{escape(item.name)}</h2><div class="cards">{cards}</div></section>'
            )

    barcoded = sorted(
        {
            shown[code.item_id].name
            for code in codes
            if code.item_id in shown and classify_code(code.code) is CodeKind.UPC
        }
    )
    footer = (
        f"Scan the barcode on the package for: {escape(', '.join(barcoded))}." if barcoded else ""
    )
    return PAGE.format(shop=escape(shop_name), sections="\n".join(sections), footer=footer)


def _card(code: Code, item: Item) -> str:
    kind = "take" if code.action is CodeAction.TAKE else "restock"
    return (
        f'<figure class="{kind}">{qr_code(code.code).svg_inline(omitsize=True)}'
        f"<figcaption><strong>{escape(label(code, item))}</strong>"
        f"<span>{escape(code.code)}</span></figcaption></figure>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Make the printable door scan sheet.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the page")
    parser.add_argument("--no-open", action="store_true", help="don't open it in the browser")
    args = parser.parse_args()

    try:
        outputs = stack_outputs(boto3.client("cloudformation"), STACK_NAME)
        store = DynamoStore(table_names(outputs))
        shop = store.get_shop(TENANT)
        if shop is None:
            print(f"Shop {TENANT!r} not found: run scripts/seed.py first.", file=sys.stderr)
            return 1
        page = build_sheet(shop.name, store.list_items(TENANT), store.list_codes(TENANT))
    except (ClientError, BotoCoreError) as error:
        print(f"AWS error: {error}\nIs your `aws login` still valid?", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"Wrote {args.out}\nPrint it from the browser (Cmd+P).")
    if not args.no_open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())

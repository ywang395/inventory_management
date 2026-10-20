"""Fill the dev tables with a test shop, so scans have something to change.

Run from the repo root, after `aws login`:

    uv run python scripts/seed.py                # add what's missing, keep current stock
    uv run python scripts/seed.py --reset-stock  # also put stock back to the sample amounts

It reads the table names and the door-1 API key id from the CafeInventory-dev stack's
outputs, so nothing is hard-coded. Dev only: the pilot shop's real items will be entered
through the dashboard. Safe to run again: records are overwritten, never duplicated.
"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

# Import the backend code (core, store) the same way the Lambdas do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from core.models import Code, CodeAction, Item, ShopSettings, Staff
from store.dynamo import DynamoStore, Shop, Station, TableNames

STACK_NAME = "CafeInventory-dev"
TENANT = "cafe-01"
STATION_ID = "door-1"

SHOP = Shop(
    tenant_id=TENANT,
    name="Test Café",
    settings=ShopSettings(timezone="America/Los_Angeles"),  # staff badges off
)

ITEMS = [
    Item("lids_12oz", "12 oz cup lids", "bag", stock=30, category="Cups & lids",
         reorder_point=10, lead_time_days=3),
    Item("cups_12oz", "12 oz hot cups", "sleeve", stock=40, category="Cups & lids",
         reorder_point=15, lead_time_days=3),
    Item("milk_whole", "Whole milk", "gallon", stock=12, category="Dairy",
         reorder_point=6, lead_time_days=1),
    Item("oat_milk", "Oat milk", "carton", stock=18, category="Dairy",
         reorder_point=8, lead_time_days=2),
    Item("espresso_beans", "Espresso beans", "5 lb bag", stock=6, category="Coffee",
         reorder_point=3, lead_time_days=5),
    Item("ice_cream_vanilla", "Vanilla ice cream", "tub", stock=5, category="Ice cream",
         reorder_point=2, lead_time_days=4),
]  # fmt: skip

TAKE, RESTOCK = CodeAction.TAKE, CodeAction.RESTOCK
CODES = [
    Code("INV-LID12-1BAG", "lids_12oz", -1, TAKE),
    Code("INV-LID12-1BOX", "lids_12oz", -12, TAKE),  # 1 box = 12 bags
    Code("INV-LID12-RESTOCK-BOX", "lids_12oz", 12, RESTOCK),
    Code("INV-CUP12-1SLV", "cups_12oz", -1, TAKE),
    Code("INV-CUP12-RESTOCK-CASE", "cups_12oz", 20, RESTOCK),  # 1 case = 20 sleeves
    Code("INV-MILK-1GAL", "milk_whole", -1, TAKE),
    Code("INV-MILK-RESTOCK-CRATE", "milk_whole", 4, RESTOCK),  # 1 crate = 4 gallons
    Code("INV-OAT-1CTN", "oat_milk", -1, TAKE),
    Code("INV-OAT-RESTOCK-CASE", "oat_milk", 12, RESTOCK),
    # A supplier barcode (UPC) printed on the oat milk carton: scanning it takes 1 carton.
    Code("012345678905", "oat_milk", -1, TAKE),
    Code("INV-BEANS-1BAG", "espresso_beans", -1, TAKE),
    Code("INV-BEANS-RESTOCK-BAG", "espresso_beans", 1, RESTOCK),
    Code("INV-ICVAN-1TUB", "ice_cream_vanilla", -1, TAKE),
    Code("INV-ICVAN-RESTOCK-TUB", "ice_cream_vanilla", 1, RESTOCK),
]

STAFF = [
    Staff("STAFF-001", "Manager"),
    Staff("STAFF-002", "Barista"),
]


def seed(store: DynamoStore, api_key_id: str, *, reset_stock: bool = False) -> list[str]:
    """Write the test shop. Returns one line per item saying what happened to its stock."""
    store.put_shop(SHOP)
    store.put_station(
        Station(api_key_id=api_key_id, tenant_id=TENANT, station_id=STATION_ID, name="Storage door")
    )

    report = []
    for item in ITEMS:
        existing = store.get_item(TENANT, item.item_id)
        if existing is None or reset_stock:
            store.put_item(TENANT, item)
            report.append(f"{item.item_id}: stock set to {item.stock} {item.smallest_unit}")
        else:
            # Keep the stock (and count flag) that scans produced; refresh everything else.
            store.put_item(
                TENANT, replace(item, stock=existing.stock, needs_count=existing.needs_count)
            )
            report.append(f"{item.item_id}: kept stock {existing.stock} {item.smallest_unit}")

    for code in CODES:
        store.put_code(TENANT, code)
    for staff in STAFF:
        store.put_staff(TENANT, staff)
    return report


def stack_outputs(cloudformation, stack_name: str) -> dict[str, str]:
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def table_names(outputs: dict[str, str]) -> TableNames:
    return TableNames(
        shops=outputs["ShopsTableName"],
        items=outputs["ItemsTableName"],
        codes=outputs["CodesTableName"],
        staff=outputs["StaffTableName"],
        stations=outputs["StationsTableName"],
        scan_log=outputs["ScanLogTableName"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the dev tables with a test shop.")
    parser.add_argument(
        "--reset-stock",
        action="store_true",
        help="put every item's stock back to the sample amount (old log entries stay)",
    )
    args = parser.parse_args()

    try:
        outputs = stack_outputs(boto3.client("cloudformation"), STACK_NAME)
        store = DynamoStore(table_names(outputs))
        report = seed(store, outputs["Door1ApiKeyId"], reset_stock=args.reset_stock)
    except (ClientError, BotoCoreError) as error:
        print(f"AWS error: {error}\nIs your `aws login` still valid?", file=sys.stderr)
        return 1

    print(f"Seeded shop {TENANT!r} in {STACK_NAME}:")
    print(f"  station {STATION_ID} <- API key id {outputs['Door1ApiKeyId']}")
    print(f"  {len(ITEMS)} items, {len(CODES)} codes, {len(STAFF)} staff badges")
    for line in report:
        print(f"    {line}")
    print(f"\nAPI URL: {outputs['ApiUrl']}")
    print(
        "API key value: aws apigateway get-api-key --api-key "
        f"{outputs['Door1ApiKeyId']} --include-value --query value --output text"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

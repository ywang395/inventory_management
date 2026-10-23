import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from app import build
from stacks import inventory_stack
from store import keys

TABLES = ["Shops", "Items", "Codes", "Staff", "Stations", "ScanLog"]


def inventory_template(stage: str = "dev") -> Template:
    app = cdk.App()
    build(app, stage, "owner@example.com")
    return Template.from_stack(app.node.find_child(f"CafeInventory-{stage}"))


def tables(template: Template) -> dict[str, dict]:
    """Table resources by construct name, e.g. "Items" (logical ids get a hash suffix)."""
    found = template.find_resources("AWS::DynamoDB::Table")
    by_name = {}
    for logical_id, resource in found.items():
        name = next(n for n in TABLES if logical_id.startswith(n))
        by_name[name] = resource
    return by_name


def key_schema(resource: dict) -> list[tuple[str, str]]:
    return [(k["AttributeName"], k["KeyType"]) for k in resource["Properties"]["KeySchema"]]


def test_there_are_six_tables() -> None:
    assert sorted(tables(inventory_template())) == sorted(TABLES)


@pytest.mark.parametrize(
    "name, schema",
    [
        ("Shops", [("pk", "HASH")]),
        ("Stations", [("pk", "HASH")]),
        ("Items", [("pk", "HASH"), ("sk", "RANGE")]),
        ("Codes", [("pk", "HASH"), ("sk", "RANGE")]),
        ("Staff", [("pk", "HASH"), ("sk", "RANGE")]),
        ("ScanLog", [("pk", "HASH"), ("sk", "RANGE")]),
    ],
)
def test_key_schema(name: str, schema: list[tuple[str, str]]) -> None:
    assert key_schema(tables(inventory_template())[name]) == schema


def test_all_tables_are_on_demand_with_generated_names() -> None:
    for name, resource in tables(inventory_template()).items():
        props = resource["Properties"]
        assert props["BillingMode"] == "PAY_PER_REQUEST", name
        assert "TableName" not in props, f"{name} should not hard-code its name"


def test_items_and_scan_log_have_streams() -> None:
    streams = {
        name: resource["Properties"].get("StreamSpecification")
        for name, resource in tables(inventory_template()).items()
    }
    # Items: before + after of every change, for stock alerts.
    assert streams.pop("Items") == {"StreamViewType": "NEW_AND_OLD_IMAGES"}
    assert streams.pop("ScanLog") == {"StreamViewType": "NEW_IMAGE"}
    assert all(stream is None for stream in streams.values())


def test_scan_log_has_a_by_item_index() -> None:
    (index,) = tables(inventory_template())["ScanLog"]["Properties"]["GlobalSecondaryIndexes"]
    assert index["IndexName"] == "by_item"
    assert key_schema({"Properties": index}) == [("item_pk", "HASH"), ("sk", "RANGE")]
    assert index["Projection"] == {"ProjectionType": "ALL"}


def test_dev_tables_are_deleted_with_the_stack() -> None:
    for name, resource in tables(inventory_template("dev")).items():
        assert resource["DeletionPolicy"] == "Delete", name
        assert not resource["Properties"].get("DeletionProtectionEnabled"), name


def test_prod_tables_are_kept_protected_and_backed_up() -> None:
    backups_on = {"PointInTimeRecoveryEnabled": True}
    for name, resource in tables(inventory_template("prod")).items():
        props = resource["Properties"]
        assert resource["DeletionPolicy"] == "Retain", name
        assert props["DeletionProtectionEnabled"] is True, name
        assert props["PointInTimeRecoverySpecification"] == backups_on, name


def test_table_names_are_stack_outputs() -> None:
    outputs = inventory_template().find_outputs("*")
    assert {f"{name}TableName" for name in TABLES} <= set(outputs)


def test_names_match_the_backend_key_module() -> None:
    # The stack and backend/store/keys.py must agree, or reads and writes would miss.
    assert inventory_stack.PK == keys.PK
    assert inventory_stack.SK == keys.SK
    assert inventory_stack.ITEM_PK == keys.ITEM_PK
    assert inventory_stack.BY_ITEM_INDEX == keys.BY_ITEM_INDEX

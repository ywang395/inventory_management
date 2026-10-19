"""The inventory backend for one stage (dev or prod).

All shops (tenants) share this stack; their data is kept apart by tenant-prefixed keys
(see backend/store/keys.py), not by separate stacks. Lambdas and the API come next.
"""

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct

# Attribute and index names. They must match backend/store/keys.py (a test checks this).
PK = "pk"
SK = "sk"
ITEM_PK = "item_pk"
BY_ITEM_INDEX = "by_item"

STRING = dynamodb.AttributeType.STRING


class InventoryStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.stage = stage
        is_prod = stage == "prod"

        def table(name: str, *, with_sort_key: bool = True, **extra) -> dynamodb.Table:
            return dynamodb.Table(
                self,
                name,
                partition_key=dynamodb.Attribute(name=PK, type=STRING),
                sort_key=dynamodb.Attribute(name=SK, type=STRING) if with_sort_key else None,
                # On-demand: pay per request, nothing to size, $0 when nobody scans.
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                # dev tables are thrown away by `cdk destroy`. prod data is kept, can't be
                # deleted by accident, and has point-in-time backups (restore any second
                # of the last 35 days; costs a little per GB, so prod only).
                removal_policy=cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY,
                deletion_protection=is_prod,
                point_in_time_recovery_specification=(
                    dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True)
                    if is_prod
                    else None
                ),
                **extra,
            )

        # One record per shop: name, time zone, staff badge settings.
        self.shops = table("Shops", with_sort_key=False)
        self.items = table("Items")
        self.codes = table("Codes")
        self.staff = table("Staff")
        # API key id -> tenant + station. Not tenant-prefixed: it's how we find the tenant.
        self.stations = table("Stations", with_sort_key=False)

        # Append-only log, keyed by station + scanned_at so a resent scan hits the same
        # key and a conditional write rejects it. The stream will feed the alerts Lambda
        # (Phase 2) with every new entry.
        self.scan_log = table("ScanLog", stream=dynamodb.StreamViewType.NEW_IMAGE)
        # One item's history across all stations (dashboard, recalculating stock).
        self.scan_log.add_global_secondary_index(
            index_name=BY_ITEM_INDEX,
            partition_key=dynamodb.Attribute(name=ITEM_PK, type=STRING),
            sort_key=dynamodb.Attribute(name=SK, type=STRING),
        )

        # CDK generates the table names; Lambdas and the seed script read them from here.
        tables = {
            "Shops": self.shops,
            "Items": self.items,
            "Codes": self.codes,
            "Staff": self.staff,
            "Stations": self.stations,
            "ScanLog": self.scan_log,
        }
        for name, t in tables.items():
            cdk.CfnOutput(self, f"{name}TableName", value=t.table_name)

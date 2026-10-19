"""The inventory backend for one stage (dev or prod).

All shops (tenants) share this stack; their data is kept apart by tenant-prefixed keys
(see backend/store/keys.py), not by separate stacks.

    Station --API key--> API Gateway (REST) --> Lambda --> DynamoDB tables
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

# The Lambda code is the whole /backend folder (core + store + handlers), minus tests.
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

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

        self._add_station_api(is_prod)

    def _add_station_api(self, is_prod: bool) -> None:
        code = lambda_.Code.from_asset(
            str(BACKEND_DIR), exclude=["tests", "**/__pycache__", "**/*.pyc"]
        )
        environment = {
            "SHOPS_TABLE": self.shops.table_name,
            "ITEMS_TABLE": self.items.table_name,
            "CODES_TABLE": self.codes.table_name,
            "STAFF_TABLE": self.staff.table_name,
            "STATIONS_TABLE": self.stations.table_name,
            "SCAN_LOG_TABLE": self.scan_log.table_name,
        }

        def function(name: str, handler: str) -> lambda_.Function:
            # Our own log group, so logs expire after 2 weeks instead of being kept
            # (and billed) forever.
            log_group = logs.LogGroup(
                self,
                f"{name}Logs",
                retention=logs.RetentionDays.TWO_WEEKS,
                removal_policy=cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY,
            )
            return lambda_.Function(
                self,
                name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                # ARM (Graviton) costs about 20% less than x86 for the same work.
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=code,
                memory_size=256,
                timeout=cdk.Duration.seconds(10),
                environment=environment,
                log_group=log_group,
            )

        # Least privilege: each function gets only the actions it needs, per table.
        self.scan_function = function("ScanFunction", "handlers.scan.handler")
        for table in (self.stations, self.shops, self.codes, self.staff):
            table.grant(self.scan_function, "dynamodb:GetItem")
        self.items.grant(self.scan_function, "dynamodb:GetItem", "dynamodb:UpdateItem")
        # Append-only log: the scan function can add and read entries, but has no
        # permission to change or delete one. AWS itself enforces this.
        self.scan_log.grant(
            self.scan_function, "dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem"
        )

        self.config_function = function("StationConfigFunction", "handlers.station_config.handler")
        for table in (self.stations, self.shops):
            table.grant(self.config_function, "dynamodb:GetItem")

        api = apigw.RestApi(
            self,
            "StationApi",
            rest_api_name=f"cafe-inventory-{self.stage}",
            description="Scanner stations send scans here (API key required).",
            # Regional: served from us-east-2 only. (Edge-optimized would add CloudFront,
            # which this account's project can't use outside its region.)
            endpoint_types=[apigw.EndpointType.REGIONAL],
            # No account-wide CloudWatch role: we don't use API Gateway access logs yet.
            cloud_watch_role=False,
            deploy_options=apigw.StageOptions(stage_name=self.stage),
            # Every method needs a valid API key; API Gateway rejects others with 403
            # before our code runs (so they cost us nothing).
            default_method_options=apigw.MethodOptions(api_key_required=True),
        )
        api.root.add_resource("scan").add_method(
            "POST", apigw.LambdaIntegration(self.scan_function)
        )
        api.root.add_resource("station").add_resource("config").add_method(
            "GET", apigw.LambdaIntegration(self.config_function)
        )

        # One API key per station. The seed script links its id to a shop and station
        # in the Stations table. Get the key's value with:
        #   aws apigateway get-api-key --api-key <Door1ApiKeyId> --include-value
        door_key = api.add_api_key(
            "Door1ApiKey", api_key_name=f"cafe-inventory-{self.stage}-door-1"
        )
        # Per-key limits: plenty for a person scanning (or an offline queue catching up),
        # but a stuck station can't run up a bill.
        plan = api.add_usage_plan(
            "StationUsagePlan",
            name=f"cafe-inventory-{self.stage}-stations",
            throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10),
            quota=apigw.QuotaSettings(limit=5000, period=apigw.Period.DAY),
        )
        plan.add_api_stage(stage=api.deployment_stage)
        plan.add_api_key(door_key)

        cdk.CfnOutput(self, "ApiUrl", value=api.url)
        cdk.CfnOutput(self, "Door1ApiKeyId", value=door_key.key_id)

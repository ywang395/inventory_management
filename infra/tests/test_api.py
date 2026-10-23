import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from app import build


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    build(app, "dev", "owner@example.com")
    return Template.from_stack(app.node.find_child("CafeInventory-dev"))


def logical_id(template: Template, resource_type: str, prefix: str) -> str:
    (found,) = [i for i in template.find_resources(resource_type) if i.startswith(prefix)]
    return found


def actions_on(template: Template, role_prefix: str, table_prefix: str) -> set[str]:
    """DynamoDB actions a function's role may perform on one table."""
    policy_id = logical_id(template, "AWS::IAM::Policy", f"{role_prefix}ServiceRoleDefaultPolicy")
    table_id = logical_id(template, "AWS::DynamoDB::Table", table_prefix)
    policy = template.find_resources("AWS::IAM::Policy")[policy_id]
    actions: set[str] = set()
    for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
        if table_id in json.dumps(statement["Resource"]):
            granted = statement["Action"]
            actions |= {granted} if isinstance(granted, str) else set(granted)
    return actions


# --- The API --------------------------------------------------------------------------


def test_api_is_regional(template: Template) -> None:
    template.has_resource_properties(
        "AWS::ApiGateway::RestApi",
        {"EndpointConfiguration": {"Types": ["REGIONAL"]}},
    )


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_every_method_needs_an_api_key(template: Template, method: str) -> None:
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": method, "ApiKeyRequired": True, "AuthorizationType": "NONE"},
    )


def test_station_key_has_a_usage_plan_with_limits(template: Template) -> None:
    template.resource_count_is("AWS::ApiGateway::ApiKey", 1)
    template.has_resource_properties(
        "AWS::ApiGateway::UsagePlan",
        {
            "Throttle": {"RateLimit": 5, "BurstLimit": 10},
            "Quota": {"Limit": 5000, "Period": "DAY"},
        },
    )
    template.resource_count_is("AWS::ApiGateway::UsagePlanKey", 1)


def test_api_url_and_key_id_are_outputs(template: Template) -> None:
    outputs = template.find_outputs("*")
    assert {"ApiUrl", "Door1ApiKeyId"} <= set(outputs)


# --- The Lambdas ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        "handlers.scan.handler",
        "handlers.station_config.handler",
        "handlers.alerts.handler",
        "handlers.items.handler",
        "handlers.log.handler",
    ],
)
def test_functions_run_python_312_on_arm(template: Template, handler: str) -> None:
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": handler,
            "Runtime": "python3.12",
            "Architectures": ["arm64"],
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "SHOPS_TABLE": Match.any_value(),
                        "ITEMS_TABLE": Match.any_value(),
                        "SCAN_LOG_TABLE": Match.any_value(),
                    }
                )
            },
        },
    )


def test_logs_are_kept_two_weeks(template: Template) -> None:
    for log_group in template.find_resources("AWS::Logs::LogGroup").values():
        assert log_group["Properties"]["RetentionInDays"] == 14


# --- Least privilege ------------------------------------------------------------------


def test_scan_function_can_only_add_and_read_log_entries(template: Template) -> None:
    # Append-only: no UpdateItem or DeleteItem on the log, enforced by IAM.
    assert actions_on(template, "ScanFunction", "ScanLog") == {
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:PutItem",
    }


def test_scan_function_can_only_read_and_update_items(template: Template) -> None:
    assert actions_on(template, "ScanFunction", "Items") == {
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
    }


@pytest.mark.parametrize("table", ["Shops", "Codes", "Staff", "Stations"])
def test_scan_function_only_reads_setup_tables(template: Template, table: str) -> None:
    assert actions_on(template, "ScanFunction", table) == {"dynamodb:GetItem"}


@pytest.mark.parametrize("table", ["Items", "Codes", "Staff", "ScanLog"])
def test_config_function_cannot_touch_inventory_tables(template: Template, table: str) -> None:
    assert actions_on(template, "StationConfigFunction", table) == set()

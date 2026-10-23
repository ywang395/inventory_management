"""The stock alerts: Items stream -> alerts Lambda -> SNS topic -> email."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from test_api import actions_on, logical_id

from app import build


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    build(app, "dev", "owner@example.com")
    return Template.from_stack(app.node.find_child("CafeInventory-dev"))


def test_owner_is_subscribed_by_email(template: Template) -> None:
    template.resource_count_is("AWS::SNS::Topic", 1)
    template.has_resource_properties(
        "AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "owner@example.com"}
    )


def test_alerts_function_reads_the_items_stream_updates_only(template: Template) -> None:
    items = logical_id(template, "AWS::DynamoDB::Table", "Items")
    template.has_resource_properties(
        "AWS::Lambda::EventSourceMapping",
        {
            "EventSourceArn": {"Fn::GetAtt": [items, "StreamArn"]},
            "StartingPosition": "LATEST",
            "FilterCriteria": {"Filters": [{"Pattern": '{"eventName":["MODIFY"]}'}]},
            "FunctionResponseTypes": ["ReportBatchItemFailures"],
            "MaximumRetryAttempts": 2,
        },
    )


def test_alerts_function_knows_the_topic(template: Template) -> None:
    topic = logical_id(template, "AWS::SNS::Topic", "StockAlerts")
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "handlers.alerts.handler",
            "Environment": {"Variables": Match.object_like({"ALERTS_TOPIC_ARN": {"Ref": topic}})},
        },
    )


def test_alerts_function_may_only_publish_to_its_topic(template: Template) -> None:
    topic = logical_id(template, "AWS::SNS::Topic", "StockAlerts")
    policy_id = logical_id(template, "AWS::IAM::Policy", "AlertsFunctionServiceRoleDefaultPolicy")
    statements = template.find_resources("AWS::IAM::Policy")[policy_id]["Properties"][
        "PolicyDocument"
    ]["Statement"]
    publish = [s for s in statements if s["Action"] == "sns:Publish"]
    assert [s["Resource"] for s in publish] == [{"Ref": topic}]


def test_alerts_function_can_read_the_shop_but_not_change_stock(template: Template) -> None:
    assert actions_on(template, "AlertsFunction", "Shops") == {"dynamodb:GetItem"}
    items_actions = actions_on(template, "AlertsFunction", "Items")
    assert items_actions  # reading the stream
    assert not any(
        action.endswith(("PutItem", "UpdateItem", "DeleteItem")) for action in items_actions
    )

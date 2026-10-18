import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from app import build

EMAIL = "owner@example.com"


def build_app(stage: str = "dev") -> cdk.App:
    app = cdk.App()
    build(app, stage, EMAIL)
    return app


def budget_template(app: cdk.App) -> Template:
    stack = app.node.find_child("CafeInventory-Budget")
    return Template.from_stack(stack)


def test_app_has_a_stage_stack_and_a_budget_stack() -> None:
    names = {stack.stack_name for stack in build_app().synth().stacks}
    assert names == {"CafeInventory-dev", "CafeInventory-Budget"}


def test_prod_stage_gets_its_own_stack_name() -> None:
    names = {stack.stack_name for stack in build_app("prod").synth().stacks}
    assert "CafeInventory-prod" in names


def test_unknown_stage_is_rejected() -> None:
    with pytest.raises(ValueError, match="stage"):
        build_app("staging")


def test_budget_is_ten_dollars_a_month_without_credits() -> None:
    budget_template(build_app()).has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": {
                "BudgetType": "COST",
                "TimeUnit": "MONTHLY",
                "BudgetLimit": {"Amount": 10, "Unit": "USD"},
                "CostTypes": {"IncludeCredit": False},
            }
        },
    )


def test_budget_emails_at_half_full_and_forecast() -> None:
    def alert(percent: int, kind: str) -> dict:
        return {
            "Notification": {
                "ComparisonOperator": "GREATER_THAN",
                "NotificationType": kind,
                "Threshold": percent,
                "ThresholdType": "PERCENTAGE",
            },
            "Subscribers": [{"SubscriptionType": "EMAIL", "Address": EMAIL}],
        }

    budget_template(build_app()).has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "NotificationsWithSubscribers": [
                alert(50, "ACTUAL"),
                alert(100, "ACTUAL"),
                alert(100, "FORECASTED"),
            ]
        },
    )


def test_stacks_are_tagged() -> None:
    assembly = build_app().synth()
    dev = assembly.get_stack_by_name("CafeInventory-dev")
    budget = assembly.get_stack_by_name("CafeInventory-Budget")

    assert dev.tags == {"Project": "cafe-inventory", "ManagedBy": "cdk", "Stage": "dev"}
    # The budget is account-wide, so it has no stage.
    assert budget.tags == {"Project": "cafe-inventory", "ManagedBy": "cdk"}

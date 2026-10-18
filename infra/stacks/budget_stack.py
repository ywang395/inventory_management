"""Account-wide monthly cost budget with email alerts.

This lives in its own stack (not in the dev stack) because a budget belongs to the whole
AWS account. Running `cdk destroy` on the dev stack should never remove the cost alarm.
Deploy it once per account.
"""

import aws_cdk as cdk
from aws_cdk import aws_budgets as budgets
from constructs import Construct


class BudgetStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        alert_email: str,
        monthly_limit_usd: int = 10,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Budgets emails the address directly; unlike SNS, there is no confirmation link.
        subscribers = [
            budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=alert_email)
        ]

        def alert(percent: int, notification_type: str):
            return budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    comparison_operator="GREATER_THAN",
                    notification_type=notification_type,  # ACTUAL = spent, FORECASTED = predicted
                    threshold=percent,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=subscribers,
            )

        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="cafe-inventory-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=monthly_limit_usd, unit="USD"),
                # By default credits count against cost, so on the Free Plan the "cost" stays
                # at $0 until the credits run out and the alarm would never fire. Leaving
                # credits out makes the budget track real usage.
                cost_types=budgets.CfnBudget.CostTypesProperty(include_credit=False),
            ),
            notifications_with_subscribers=[
                alert(50, "ACTUAL"),  # $5 spent
                alert(100, "ACTUAL"),  # $10 spent
                alert(100, "FORECASTED"),  # on track to pass $10 this month
            ],
        )

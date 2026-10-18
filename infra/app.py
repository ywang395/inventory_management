"""CDK entry point. Run from /infra, e.g. `npx cdk synth` or `npx cdk deploy -c stage=dev`."""

import os
import sys

import aws_cdk as cdk

from stacks.budget_stack import BudgetStack
from stacks.inventory_stack import InventoryStack

STAGES = ("dev", "prod")


def build(app: cdk.App, stage: str, alert_email: str) -> None:
    """Add all stacks to `app`. Kept separate from the code below so tests can call it."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}; use one of {', '.join(STAGES)}")

    # Without AWS credentials these are None and CDK synthesizes an environment-agnostic
    # template, so `cdk synth` and the tests work offline. `cdk deploy` fills them in
    # from your AWS CLI profile.
    env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION"),
    )

    inventory = InventoryStack(app, f"CafeInventory-{stage}", stage=stage, env=env)
    cdk.Tags.of(inventory).add("Stage", stage)

    BudgetStack(app, "CafeInventory-Budget", alert_email=alert_email, env=env)

    # Tags make it easy to find (and price) everything this project created.
    # (The budget resource itself isn't taggable this way; its stack still is.)
    cdk.Tags.of(app).add("Project", "cafe-inventory")
    cdk.Tags.of(app).add("ManagedBy", "cdk")


def main() -> None:
    app = cdk.App()
    stage = app.node.try_get_context("stage") or "dev"
    alert_email = os.environ.get("BUDGET_ALERT_EMAIL")
    if not alert_email:
        sys.exit("BUDGET_ALERT_EMAIL is not set. Copy .env.example to .env and fill it in.")
    build(app, stage, alert_email)
    app.synth()


if __name__ == "__main__":
    main()

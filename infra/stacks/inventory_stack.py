"""The inventory backend for one stage (dev or prod).

All shops (tenants) share this stack; their data is kept apart by tenant-prefixed keys,
not by separate stacks. DynamoDB tables come in the next task, then the POST /scan API.
"""

import aws_cdk as cdk
from constructs import Construct


class InventoryStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.stage = stage

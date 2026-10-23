"""Invite an owner to the dashboard: creates their login for the test shop.

Run from the repo root, after `aws login` (the email defaults to BUDGET_ALERT_EMAIL):

    uv run --env-file .env python scripts/create_owner.py
    uv run --env-file .env python scripts/create_owner.py --email manager@example.com
    uv run --env-file .env python scripts/create_owner.py --resend   # invitation expired

Cognito emails "Your Café Inventory login" with a temporary password; the owner picks
their own password at the first sign-in. The login is tied to the shop for good: the
shop id (custom:tenant_id) can't be changed later, not even by us.
"""

import argparse
import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from seed import STACK_NAME, TENANT, stack_outputs


def owner_attributes(email: str, tenant_id: str) -> list[dict[str, str]]:
    return [
        {"Name": "email", "Value": email},
        # We send the invitation to this address, and signing in needs the password in
        # it, so the address is proven when they first sign in.
        {"Name": "email_verified", "Value": "true"},
        {"Name": "custom:tenant_id", "Value": tenant_id},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Invite an owner to the dashboard.")
    parser.add_argument("--email", default=os.environ.get("BUDGET_ALERT_EMAIL"))
    parser.add_argument("--resend", action="store_true", help="send the invitation again")
    args = parser.parse_args()
    if not args.email:
        print("No email: pass --email or set BUDGET_ALERT_EMAIL in .env.", file=sys.stderr)
        return 1

    cognito = boto3.client("cognito-idp")
    try:
        pool_id = stack_outputs(boto3.client("cloudformation"), STACK_NAME)["OwnerPoolId"]
        invite = {
            "UserPoolId": pool_id,
            "Username": args.email,
            "DesiredDeliveryMediums": ["EMAIL"],
        }
        if args.resend:
            cognito.admin_create_user(**invite, MessageAction="RESEND")
        else:
            cognito.admin_create_user(**invite, UserAttributes=owner_attributes(args.email, TENANT))
    except cognito.exceptions.UsernameExistsException:
        print(f"{args.email} already has a login. Use --resend to send the invitation again.")
        return 1
    except (ClientError, BotoCoreError) as error:
        print(f"AWS error: {error}\nIs your `aws login` still valid?", file=sys.stderr)
        return 1

    print(
        f"Invited {args.email} to shop {TENANT!r}: check the inbox for 'Your Café Inventory login'."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

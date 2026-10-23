"""The owners' login (Cognito): invite-only, tied to one shop, safe for a browser app."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from app import build


def template_for(stage: str) -> Template:
    app = cdk.App()
    build(app, stage, "owner@example.com")
    return Template.from_stack(app.node.find_child(f"CafeInventory-{stage}"))


@pytest.fixture(scope="module")
def template() -> Template:
    return template_for("dev")


def test_only_we_can_create_logins(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "AdminCreateUserConfig": Match.object_like({"AllowAdminCreateUserOnly": True}),
            "UsernameAttributes": ["email"],
        },
    )


def test_every_login_has_a_shop_that_cant_change(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Cognito::UserPool",
        {"Schema": [Match.object_like({"Name": "tenant_id", "Mutable": False})]},
    )


def test_strong_passwords(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Cognito::UserPool",
        {"Policies": {"PasswordPolicy": Match.object_like({"MinimumLength": 12})}},
    )


def test_dashboard_client_is_a_public_client_with_the_code_flow(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "GenerateSecret": False,  # a browser can't keep a secret
            "AllowedOAuthFlows": ["code"],  # code + PKCE, not the old implicit flow
            "CallbackURLs": ["http://localhost:5173/"],
            "EnableTokenRevocation": True,
        },
    )


def test_dashboard_can_read_but_not_write_the_shop_id(template: Template) -> None:
    (client,) = template.find_resources("AWS::Cognito::UserPoolClient").values()
    props = client["Properties"]
    assert "custom:tenant_id" in props["ReadAttributes"]
    assert "custom:tenant_id" not in props["WriteAttributes"]


def test_login_page_is_managed_login_with_a_style(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Cognito::UserPoolDomain",
        {"Domain": "cafe-inventory-ywang395-dev", "ManagedLoginVersion": 2},
    )
    template.has_resource_properties(
        "AWS::Cognito::ManagedLoginBranding", {"UseCognitoProvidedValues": True}
    )


def test_dashboard_settings_are_outputs(template: Template) -> None:
    outputs = template.find_outputs("*")
    assert {"OwnerPoolId", "DashboardClientId", "LoginDomainUrl", "OwnerPoolIssuerUrl"} <= set(
        outputs
    )


def test_dev_pool_is_quick_to_test_and_thrown_away() -> None:
    dev = template_for("dev")
    dev.has_resource_properties("AWS::Cognito::UserPool", {"MfaConfiguration": "OFF"})
    dev.has_resource("AWS::Cognito::UserPool", {"DeletionPolicy": "Delete"})


def test_prod_pool_requires_an_authenticator_code_and_is_kept() -> None:
    prod = template_for("prod")
    prod.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "MfaConfiguration": "ON",
            "EnabledMfas": ["SOFTWARE_TOKEN_MFA"],
            "DeletionProtection": "ACTIVE",
        },
    )
    prod.has_resource("AWS::Cognito::UserPool", {"DeletionPolicy": "Retain"})

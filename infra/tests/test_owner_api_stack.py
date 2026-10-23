"""The owner API: its own REST API, Cognito login on every GET, CORS for the dashboard."""

import json

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


def owner_methods(template: Template) -> list[dict]:
    api = logical_id(template, "AWS::ApiGateway::RestApi", "OwnerApi")
    return [
        method["Properties"]
        for method in template.find_resources("AWS::ApiGateway::Method").values()
        if method["Properties"]["RestApiId"] == {"Ref": api}
    ]


def test_owner_api_is_separate_from_the_station_api(template: Template) -> None:
    template.resource_count_is("AWS::ApiGateway::RestApi", 2)
    template.has_resource_properties(
        "AWS::ApiGateway::RestApi", {"Name": "cafe-inventory-dev-owner"}
    )


def test_every_get_needs_an_owner_login(template: Template) -> None:
    authorizer = logical_id(template, "AWS::ApiGateway::Authorizer", "OwnerAuthorizer")
    gets = [m for m in owner_methods(template) if m["HttpMethod"] == "GET"]
    assert len(gets) == 2
    for method in gets:
        assert method["AuthorizationType"] == "COGNITO_USER_POOLS"
        assert method["AuthorizerId"] == {"Ref": authorizer}
        assert not method.get("ApiKeyRequired")


def test_authorizer_trusts_only_the_owner_pool(template: Template) -> None:
    pool = logical_id(template, "AWS::Cognito::UserPool", "OwnerPool")
    template.has_resource_properties(
        "AWS::ApiGateway::Authorizer",
        {
            "Type": "COGNITO_USER_POOLS",
            "ProviderARNs": [{"Fn::GetAtt": [pool, "Arn"]}],
            "IdentitySource": "method.request.header.Authorization",
        },
    )


def test_browser_preflight_needs_no_login_and_allows_only_the_dashboard(
    template: Template,
) -> None:
    preflights = [m for m in owner_methods(template) if m["HttpMethod"] == "OPTIONS"]
    assert preflights
    for method in preflights:
        assert method["AuthorizationType"] == "NONE"
        assert "http://localhost:5173" in json.dumps(method["Integration"])
        assert "'*'" not in json.dumps(method["Integration"])


def test_api_gateway_errors_are_readable_by_the_browser(template: Template) -> None:
    for response_type in ("DEFAULT_4XX", "DEFAULT_5XX"):
        template.has_resource_properties(
            "AWS::ApiGateway::GatewayResponse",
            {
                "ResponseType": response_type,
                "ResponseParameters": {"gatewayresponse.header.Access-Control-Allow-Origin": "'*'"},
            },
        )


def test_owner_api_is_throttled(template: Template) -> None:
    template.has_resource_properties(
        "AWS::ApiGateway::Stage",
        {
            "StageName": "dev",
            "MethodSettings": Match.array_with(
                [Match.object_like({"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20})]
            ),
        },
    )


def test_owner_functions_know_the_dashboard_address(template: Template) -> None:
    for handler in ("handlers.items.handler", "handlers.log.handler"):
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Handler": handler,
                "Environment": {
                    "Variables": Match.object_like({"DASHBOARD_ORIGINS": "http://localhost:5173"})
                },
            },
        )


def test_owner_functions_are_read_only(template: Template) -> None:
    assert actions_on(template, "OwnerItemsFunction", "Items") == {"dynamodb:Query"}
    assert actions_on(template, "OwnerItemsFunction", "Shops") == {"dynamodb:GetItem"}
    assert actions_on(template, "OwnerItemsFunction", "ScanLog") == set()
    assert actions_on(template, "OwnerLogFunction", "ScanLog") == {"dynamodb:Query"}
    assert actions_on(template, "OwnerLogFunction", "Items") == {"dynamodb:GetItem"}


def test_owner_api_url_is_an_output(template: Template) -> None:
    assert "OwnerApiUrl" in template.find_outputs("*")

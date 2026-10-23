"""The inventory backend for one stage (dev or prod).

All shops (tenants) share this stack; their data is kept apart by tenant-prefixed keys
(see backend/store/keys.py), not by separate stacks.

    Station --API key--> API Gateway (REST) --> Lambda --> DynamoDB tables
    Items table stream --> alerts Lambda --> SNS topic --> owner's email
    Owner --Cognito login page--> ID token --> owner API (REST) --> Lambda --> DynamoDB
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct

# The Lambda code is the whole /backend folder (core + store + handlers), minus tests.
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

# Attribute and index names. They must match backend/store/keys.py (a test checks this).
PK = "pk"
SK = "sk"
ITEM_PK = "item_pk"
BY_ITEM_INDEX = "by_item"

STRING = dynamodb.AttributeType.STRING

# Owners sign in on Cognito's own login page at https://<prefix>.auth.<region>.amazoncognito.com.
# The prefix must be unique among all Cognito users in the region, hence the suffix.
LOGIN_DOMAIN_PREFIX = "cafe-inventory-ywang395"
# Where the login page may send people back to (and where sign-out lands). Must match
# exactly, trailing "/" included. Plain HTTP is only allowed for localhost (development).
DASHBOARD_URLS = ["http://localhost:5173/"]
# The same addresses as browser origins (no trailing "/"), for CORS on the owner API.
DASHBOARD_ORIGINS = [url.rstrip("/") for url in DASHBOARD_URLS]


class InventoryStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, stage: str, alert_email: str, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.stage = stage
        self.is_prod = is_prod = stage == "prod"

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
        # The stream hands every change to an item, before and after, to the alerts
        # Lambda: "stock went from 11 to 10" is exactly what a low-stock alert needs.
        self.items = table("Items", stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES)
        self.codes = table("Codes")
        self.staff = table("Staff")
        # API key id -> tenant + station. Not tenant-prefixed: it's how we find the tenant.
        self.stations = table("Stations", with_sort_key=False)

        # Append-only log, keyed by station + scanned_at so a resent scan hits the same
        # key and a conditional write rejects it. Its stream (every new entry) isn't
        # used yet; it's free and kept for later uses such as usage statistics.
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

        # The Lambda code is one asset (the whole /backend folder) shared by every function.
        self._code = lambda_.Code.from_asset(
            str(BACKEND_DIR), exclude=["tests", "**/__pycache__", "**/*.pyc"]
        )
        self._environment = {
            "SHOPS_TABLE": self.shops.table_name,
            "ITEMS_TABLE": self.items.table_name,
            "CODES_TABLE": self.codes.table_name,
            "STAFF_TABLE": self.staff.table_name,
            "STATIONS_TABLE": self.stations.table_name,
            "SCAN_LOG_TABLE": self.scan_log.table_name,
        }
        self._add_station_api()
        self._add_alerts(alert_email)
        self._add_owner_login()
        self._add_owner_api()

    def _function(self, name: str, handler: str) -> lambda_.Function:
        # Our own log group, so logs expire after 2 weeks instead of being kept
        # (and billed) forever.
        log_group = logs.LogGroup(
            self,
            f"{name}Logs",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=cdk.RemovalPolicy.RETAIN if self.is_prod else cdk.RemovalPolicy.DESTROY,
        )
        return lambda_.Function(
            self,
            name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            # ARM (Graviton) costs about 20% less than x86 for the same work.
            architecture=lambda_.Architecture.ARM_64,
            handler=handler,
            code=self._code,
            memory_size=256,
            timeout=cdk.Duration.seconds(10),
            environment=self._environment,
            log_group=log_group,
        )

    def _add_station_api(self) -> None:
        function = self._function
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

    def _add_alerts(self, alert_email: str) -> None:
        # SNS fans one message out to every subscriber. For now that's one email; AWS
        # sends it a "Confirm subscription" email first, and nothing arrives until the
        # link in it is clicked. Each message carries the shop's tenant_id, so later
        # every shop owner can subscribe with a filter policy for just their shop.
        self.alerts_topic = sns.Topic(
            self, "StockAlerts", display_name="Cafe inventory alerts"
        )  # display_name is the email's sender name
        self.alerts_topic.add_subscription(subscriptions.EmailSubscription(alert_email))

        self.alerts_function = self._function("AlertsFunction", "handlers.alerts.handler")
        self.alerts_function.add_environment("ALERTS_TOPIC_ARN", self.alerts_topic.topic_arn)
        self.alerts_topic.grant_publish(self.alerts_function)
        self.shops.grant(self.alerts_function, "dynamodb:GetItem")  # shop name for the email

        # Lambda polls the Items stream for us (no charge for the polling) and calls the
        # function with batches of changes. Read access to the stream is granted here.
        self.alerts_function.add_event_source(
            event_sources.DynamoEventSource(
                self.items,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
                # Only item updates matter (a new item hasn't dropped below anything);
                # filtered records never start the function, so they cost nothing.
                filters=[
                    lambda_.FilterCriteria.filter(
                        {"eventName": lambda_.FilterRule.is_equal("MODIFY")}
                    )
                ],
                # The function reports which record failed; Lambda retries from there
                # (twice), so records before it aren't emailed again.
                report_batch_item_failures=True,
                retry_attempts=2,
            )
        )

    def _add_owner_login(self) -> None:
        removal = cdk.RemovalPolicy.RETAIN if self.is_prod else cdk.RemovalPolicy.DESTROY
        self.user_pool = cognito.UserPool(
            self,
            "OwnerPool",
            user_pool_name=f"cafe-inventory-{self.stage}-owners",
            # Owners and managers are invited (scripts/create_owner.py); nobody can sign
            # themselves up, so nobody can attach themselves to a shop.
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            sign_in_case_sensitive=False,
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=cdk.Duration.days(7),
            ),
            # The shop a login belongs to. Set once when the user is created and never
            # changeable afterwards, not even by the user, so the owner API can trust it
            # (it arrives in the ID token as "custom:tenant_id").
            custom_attributes={
                "tenant_id": cognito.StringAttribute(min_len=1, max_len=64, mutable=False)
            },
            # Essentials: needed for the newer managed login page; free up to 10,000
            # monthly active users.
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            # Prod asks for an authenticator-app code at every sign-in. Dev leaves it off
            # (a development-only exception) so testing stays quick.
            mfa=cognito.Mfa.REQUIRED if self.is_prod else cognito.Mfa.OFF,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False)
            if self.is_prod
            else None,
            user_invitation=cognito.UserInvitationConfig(
                email_subject="Your Café Inventory login",
                email_body=(
                    "Hello! Your Café Inventory login is {username} and your temporary "
                    "password is {####} . You'll choose your own password the first time "
                    "you sign in."
                ),
            ),
            deletion_protection=self.is_prod,
            removal_policy=removal,
        )

        domain = self.user_pool.add_domain(
            "LoginDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"{LOGIN_DOMAIN_PREFIX}-{self.stage}"
            ),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
        )

        self.dashboard_client = self.user_pool.add_client(
            "DashboardClient",
            user_pool_client_name=f"dashboard-{self.stage}",
            # A browser app can't keep a secret, so it has none. Sign-in uses the
            # authorization code flow with PKCE, which is safe without one.
            generate_secret=False,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=DASHBOARD_URLS,
                logout_urls=DASHBOARD_URLS,
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            auth_flows=cognito.AuthFlow(user_srp=True),
            prevent_user_existence_errors=True,  # "wrong email or password", never which one
            enable_token_revocation=True,  # signing out really ends the session
            id_token_validity=cdk.Duration.hours(1),
            access_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.hours(12),  # about a work day
            # The dashboard can read the shop id (it's in the ID token) but never write it.
            read_attributes=cognito.ClientAttributes()
            .with_standard_attributes(email=True, email_verified=True)
            .with_custom_attributes("tenant_id"),
            write_attributes=cognito.ClientAttributes().with_standard_attributes(email=True),
        )

        # Managed login needs a style for each app client, or its login page doesn't
        # work. These are Cognito's default colors and layout; they can be changed later.
        branding = cognito.CfnManagedLoginBranding(
            self,
            "DashboardLoginBranding",
            user_pool_id=self.user_pool.user_pool_id,
            client_id=self.dashboard_client.user_pool_client_id,
            use_cognito_provided_values=True,
        )
        branding.node.add_dependency(domain)

        # The dashboard needs these (they're not secrets: the login page is public).
        cdk.CfnOutput(self, "OwnerPoolId", value=self.user_pool.user_pool_id)
        cdk.CfnOutput(self, "DashboardClientId", value=self.dashboard_client.user_pool_client_id)
        cdk.CfnOutput(self, "LoginDomainUrl", value=domain.base_url())
        cdk.CfnOutput(self, "OwnerPoolIssuerUrl", value=self.user_pool.user_pool_provider_url)

    def _add_owner_api(self) -> None:
        # A separate API from the stations': each API has one kind of caller (stations
        # with API keys, owners with a login), so neither can weaken the other.
        api = apigw.RestApi(
            self,
            "OwnerApi",
            rest_api_name=f"cafe-inventory-{self.stage}-owner",
            description="The owner dashboard reads stock here (Cognito login required).",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            cloud_watch_role=False,
            deploy_options=apigw.StageOptions(
                stage_name=self.stage,
                # A dashboard needs a handful of requests a minute; this caps runaway costs.
                throttling_rate_limit=10,
                throttling_burst_limit=20,
            ),
            # Every method needs a valid ID token from the owners' user pool. API Gateway
            # checks it (signature, expiry, issuer) before our code runs and hands us its
            # claims, including the shop: custom:tenant_id.
            default_method_options=apigw.MethodOptions(
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=apigw.CognitoUserPoolsAuthorizer(
                    self, "OwnerAuthorizer", cognito_user_pools=[self.user_pool]
                ),
            ),
            # The dashboard runs on another address, so the browser first asks the API
            # (an OPTIONS "preflight", which carries no token) whether it may call it.
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=DASHBOARD_ORIGINS,
                allow_methods=["GET"],
                allow_headers=["Authorization", "Content-Type"],
                max_age=cdk.Duration.hours(1),
            ),
        )
        # Errors from API Gateway itself (no or expired token, throttled) carry no data,
        # but without a CORS header the browser hides them behind a vague "CORS error".
        for name, response_type in (
            ("Default4xx", apigw.ResponseType.DEFAULT_4_XX),
            ("Default5xx", apigw.ResponseType.DEFAULT_5_XX),
        ):
            api.add_gateway_response(
                name,
                type=response_type,
                response_headers={"Access-Control-Allow-Origin": "'*'"},
            )

        items_function = self._function("OwnerItemsFunction", "handlers.items.handler")
        log_function = self._function("OwnerLogFunction", "handlers.log.handler")
        for function in (items_function, log_function):
            function.add_environment("DASHBOARD_ORIGINS", ",".join(DASHBOARD_ORIGINS))

        # Least privilege, read-only: the dashboard can look, never change stock.
        self.shops.grant(items_function, "dynamodb:GetItem")
        self.items.grant(items_function, "dynamodb:Query")
        self.items.grant(log_function, "dynamodb:GetItem")
        self.scan_log.grant(log_function, "dynamodb:Query")  # incl. the by_item index

        api.root.add_resource("items").add_method("GET", apigw.LambdaIntegration(items_function))
        api.root.add_resource("log").add_method("GET", apigw.LambdaIntegration(log_function))

        cdk.CfnOutput(self, "OwnerApiUrl", value=api.url)

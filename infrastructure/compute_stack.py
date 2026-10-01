"""
Application Tier

A serverless API that answers requests, with no servers to manage.

Pay only for what you use
Your code runs only when a request arrives, so you pay for compute only
while it is working. Traffic limits and a cap on simultaneous copies
protect the application and database from sudden spikes.

Private by design
The application runs in the private application zone. It reaches the
database directly and AWS services through the NAT gateway; nothing can
reach in.

Approved callers only
Requests must come from an AWS identity you have approved through IAM.
The health check is the one open route, so uptime monitors can reach it,
and it reveals nothing beyond "ok" or "unavailable".

Least-privilege access
Beyond tracing, logging and networking, the application can read exactly
one thing: its database login. AWS's Parameters and Secrets extension
fetches that login at run time and caches it briefly; it is never stored
in code.

Full visibility
Every API request is logged, requests are traced end to end, and alarms
warn the team about errors, capped traffic and server failures (by
email, once an address is set at deploy time).
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig

RUNTIME_DIR = Path(__file__).resolve().parent.parent / "runtime"


class ComputeStack(Stack):
    """Rate-limited REST API, private Lambda function and alarms."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: "DemoConfig",
        vpc: ec2.IVpc,
        lambda_security_group: ec2.ISecurityGroup,
        db_secret: secretsmanager.ISecret,
        alarm_topic: sns.ITopic,
        **kwargs: Any,
    ) -> None:
        """Create the API and a function that reads only ``db_secret``.

        Alarms go to ``alarm_topic``; every size, limit and retention
        period comes from ``config``.
        """
        super().__init__(scope, construct_id, **kwargs)

        handler_logs = logs.LogGroup(
            self,
            "HandlerLogs",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )

        self.handler = lambda_.Function(
            self,
            "Handler",
            runtime=lambda_.Runtime.PYTHON_3_14,
            # Lower running costs with efficient AWS Graviton
            # processors.
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(RUNTIME_DIR)),
            memory_size=config.lambda_memory_mb,
            timeout=Duration.seconds(config.lambda_timeout_seconds),
            # Caps how many copies run at once, so a burst can't
            # overwhelm the database.
            reserved_concurrent_executions=config.lambda_reserved_concurrency,
            # Runs in the application zone: it can reach out, but
            # nothing can reach in.
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[lambda_security_group],
            log_group=handler_logs,
            # Traces show where time is spent on every request.
            tracing=lambda_.Tracing.ACTIVE,
            # AWS's extension fetches the database login and caches it
            # briefly.
            params_and_secrets=lambda_.ParamsAndSecretsLayerVersion.from_version(
                lambda_.ParamsAndSecretsVersions.V1_0_103,
                secrets_manager_ttl=Duration.minutes(
                    config.secret_cache_minutes
                ),
            ),
            # Only the login's location is shared with the application,
            # never the password itself.
            environment={"DB_SECRET_ARN": db_secret.secret_arn},
        )

        # The application may read its database login, and nothing else
        # of yours.
        db_secret.grant_read(self.handler)

        # Every API request is logged with its time, address, route and
        # result.
        access_logs = logs.LogGroup(
            self,
            "ApiAccessLogs",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )

        if config.api_iam_auth:
            # Only AWS identities you approve through IAM can call the
            # API.
            authorization = apigw.AuthorizationType.IAM
        else:
            authorization = apigw.AuthorizationType.NONE

        self.api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=self.handler,
            # Only the routes declared below exist; API Gateway rejects
            # everything else.
            proxy=False,
            # API Gateway needs an account-wide logging role to write
            # its logs. It is removed with the stack; every other API in
            # this account and Region loses logging until the role is
            # recreated.
            cloud_watch_role=True,
            cloud_watch_role_removal_policy=config.removal_policy,
            default_method_options=apigw.MethodOptions(
                authorization_type=authorization
            ),
            deploy_options=apigw.StageOptions(
                stage_name=config.env_name,
                # Traffic limits keep the application and database
                # steady during spikes.
                throttling_rate_limit=config.api_rate_limit,
                throttling_burst_limit=config.api_burst_limit,
                tracing_enabled=True,
                logging_level=apigw.MethodLoggingLevel.ERROR,
                access_log_destination=apigw.LogGroupLogDestination(
                    access_logs
                ),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=False,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=False,
                ),
            ),
        )

        # GET /items confirms the app can fetch its login and reach the
        # database. Like every route added later, it follows the sign-in
        # rule above.
        self.api.root.add_resource("items").add_method("GET")

        # GET /health runs the same check but stays open, so uptime
        # monitors can reach it. It shows only "ok" or "unavailable".
        self.health_method = self.api.root.add_resource("health").add_method(
            "GET", authorization_type=apigw.AuthorizationType.NONE
        )

        # API Gateway's own error logs go to a group this stack owns,
        # created before the stage starts writing, so they follow the
        # same retention and clean-up.
        execution_logs = logs.LogGroup(
            self,
            "ApiExecutionLogs",
            log_group_name=(
                f"API-Gateway-Execution-Logs_{self.api.rest_api_id}/{config.env_name}"
            ),
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )
        self.api.deployment_stage.node.add_dependency(execution_logs)

        # Alarms send warnings to the shared alert channel.
        alarm_action = cloudwatch_actions.SnsAction(alarm_topic)
        period = Duration.minutes(config.metric_period_minutes)

        errors_alarm = cloudwatch.Alarm(
            self,
            "HandlerErrors",
            metric=self.handler.metric_errors(period=period),
            threshold=config.lambda_error_alarm_count,
            evaluation_periods=config.alarm_evaluation_periods,
            alarm_description=(
                f"{config.lambda_error_alarm_count} or more Lambda errors "
                f"in {config.metric_period_minutes} minutes"
            ),
        )
        throttles_alarm = cloudwatch.Alarm(
            self,
            "HandlerThrottles",
            metric=self.handler.metric_throttles(period=period),
            # Any turned-away request counts.
            threshold=1,
            evaluation_periods=config.alarm_evaluation_periods,
            alarm_description=(
                "Lambda is turning requests away at its concurrency cap"
            ),
        )
        server_errors_alarm = cloudwatch.Alarm(
            self,
            "Api5xx",
            metric=self.api.metric_server_error(period=period),
            threshold=config.api_5xx_alarm_count,
            evaluation_periods=config.alarm_evaluation_periods,
            alarm_description=(
                f"{config.api_5xx_alarm_count} or more API server errors "
                f"in {config.metric_period_minutes} minutes"
            ),
        )
        for alarm in (errors_alarm, throttles_alarm, server_errors_alarm):
            alarm.add_alarm_action(alarm_action)

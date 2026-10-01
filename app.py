#!/usr/bin/env python3
"""
Secure Serverless API

Brings the network, database, application and monitoring tiers together in
one deployment. Every size, limit and retention period lives in DemoConfig
below, so moving to production means changing settings, not rewriting stacks.
"""

import os
from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs

from infrastructure.compute_stack import ComputeStack
from infrastructure.database_stack import DatabaseStack
from infrastructure.monitoring_stack import MonitoringStack
from infrastructure.vpc_stack import VpcStack


@dataclass(frozen=True)
class DemoConfig:
    """Every size, limit, retention period and removal policy for one environment."""

    # Naming
    env_name: str = "demo"
    project: str = "cdk-demo"

    # Network: two AWS locations and a single shared NAT gateway to keep costs down
    vpc_cidr: str = "10.0.0.0/16"
    subnet_cidr_mask: int = 24
    max_azs: int = 2
    nat_gateways: int = 1

    # Database: smallest Graviton size, one location, short backup history
    db_instance_type: ec2.InstanceType = ec2.InstanceType.of(
        ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO
    )
    db_multi_az: bool = False
    db_storage_gib: int = 20
    db_max_storage_gib: int = 50
    db_backup_days: int = 1
    db_deletion_protection: bool = False
    # The database alone can keep a final backup (SNAPSHOT) when it is deleted.
    db_removal_policy: cdk.RemovalPolicy = cdk.RemovalPolicy.DESTROY
    db_performance_insights: bool = False  # not available on the micro size
    secret_rotation_days: int = 30

    # Application
    lambda_memory_mb: int = 256
    lambda_timeout_seconds: int = 15
    lambda_reserved_concurrency: int = 10
    secret_cache_minutes: int = 5
    api_rate_limit: int = 50
    api_burst_limit: int = 100
    api_iam_auth: bool = True  # every route except GET /health needs IAM

    # Alerts and dashboard
    alarm_email: str | None = None
    metric_period_minutes: int = 5
    alarm_evaluation_periods: int = 1
    db_cpu_alarm_percent: int = 80
    db_cpu_alarm_periods: int = 3
    db_free_storage_alarm_gib: int = 2
    lambda_error_alarm_count: int = 5
    api_5xx_alarm_count: int = 5

    # Housekeeping: what happens to logs and other resources when stacks are deleted
    log_retention: logs.RetentionDays = logs.RetentionDays.ONE_WEEK
    removal_policy: cdk.RemovalPolicy = cdk.RemovalPolicy.DESTROY

    def __post_init__(self) -> None:
        """Reject settings that CloudFormation would refuse at deploy time."""
        if self.removal_policy == cdk.RemovalPolicy.SNAPSHOT:
            raise ValueError(
                "removal_policy cannot be SNAPSHOT: log groups only support DESTROY "
                "or RETAIN. Use db_removal_policy for a final database snapshot."
            )


app = cdk.App()
config = DemoConfig()

# Deploys to the AWS account and region of your current AWS CLI profile.
env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

# Every resource is labeled for cost reports and ownership.
cdk.Tags.of(app).add("Environment", config.env_name)
cdk.Tags.of(app).add("Project", config.project)
cdk.Tags.of(app).add("ManagedBy", "CDK")

# Each tier is its own stack. Dependencies flow one way:
# network -> database -> application -> monitoring.
network = VpcStack(app, f"{config.project}-vpc", config=config, env=env)
database = DatabaseStack(
    app,
    f"{config.project}-database",
    config=config,
    vpc=network.vpc,
    lambda_security_group=network.lambda_sg,
    alarm_topic=network.alarm_topic,
    env=env,
)
compute = ComputeStack(
    app,
    f"{config.project}-compute",
    config=config,
    vpc=network.vpc,
    lambda_security_group=network.lambda_sg,
    db_secret=database.secret,
    alarm_topic=network.alarm_topic,
    env=env,
)
MonitoringStack(
    app,
    f"{config.project}-monitoring",
    config=config,
    database=database.instance,
    handler=compute.handler,
    api=compute.api,
    env=env,
)

# Optional security scan: `cdk synth -c nag=1`
if app.node.try_get_context("nag") is not None:
    from cdk_nag import AwsSolutionsChecks, NagPackSuppression, NagSuppressions

    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

    # Findings accepted for the demo, each with its reason and production fix (see
    # "Accepted demo findings" in docs/cdk-well-architected.md). New ones still fail.
    managed_policy = "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/"
    lambda_logging = managed_policy + "AWSLambdaBasicExecutionRole"
    NagSuppressions.add_stack_suppressions(
        database,
        [
            NagPackSuppression(
                id="AwsSolutions-RDS3", reason="Single-AZ demo; set db_multi_az=True"
            ),
            NagPackSuppression(
                id="AwsSolutions-RDS10",
                reason="No deletion protection in the demo; set db_deletion_protection",
            ),
            NagPackSuppression(
                id="AwsSolutions-RDS11", reason="Default port 5432; optional to change"
            ),
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason="AWS managed logging policy on CDK's log-retention helper",
                applies_to=[lambda_logging],
            ),
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="CDK's log-retention helper needs * to set RDS log retention",
                applies_to=["Resource::*"],
            ),
        ],
    )
    NagSuppressions.add_stack_suppressions(
        compute,
        [
            NagPackSuppression(
                id="AwsSolutions-APIG2", reason="No request validation; add per route"
            ),
            NagPackSuppression(
                id="AwsSolutions-APIG3",
                reason="No WAF in the demo; associate a WAFv2 web ACL",
            ),
            NagPackSuppression(
                id="AwsSolutions-COG4",
                reason="Routes use IAM authorization instead of Cognito",
            ),
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason="AWS managed policies for Lambda, VPC and API Gateway logging",
                applies_to=[
                    lambda_logging,
                    managed_policy + "AWSLambdaVPCAccessExecutionRole",
                    managed_policy + "AmazonAPIGatewayPushToCloudWatchLogs",
                ],
            ),
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="X-Ray tracing requires Resource *",
                applies_to=["Resource::*"],
            ),
        ],
    )
    # Only the health check is open; any other route without sign-in still fails.
    NagSuppressions.add_resource_suppressions(
        compute.health_method,
        [
            NagPackSuppression(
                id="AwsSolutions-APIG4",
                reason="Open for uptime monitors; returns only ok or unavailable",
            ),
        ],
    )

app.synth()

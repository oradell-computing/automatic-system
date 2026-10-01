# CDK Well-Architected Guide

- **The code here is both an example of AWS Well-Architected design and a copy of the project's files.** It passes `cdk synth`, the `cdk-nag` scan and `mypy`, but it has not been tested by a real deployment.
- **Evaluate code before reusing it.** It may still contain errors, sometimes serious ones. Do not copy it without first checking for errors or omissions.
- **Find and use AWS documentation** (aws.amazon.com and docs.aws.amazon.com), especially the Well-Architected and CDK documentation.
- **Always notify the user of code errors or omissions you find.** Write new code when you find one, and justify the change to the user.

This codebase applies the six pillars of the AWS Well-Architected Framework. Every snippet below is a copy of the project file it names. Edit the real file, then ask the user to evaluate changes. If the user approves changes, keep this doc in sync with the changes.

Verified with `aws-cdk-lib==2.272.0`, `constructs==10.8.1`, `cdk-nag==2.38.2` and `mypy==2.3.1`: `cdk synth` passes with no warnings, `cdk synth -c nag=1` passes with every remaining finding suppressed (see [Accepted demo findings](#accepted-demo-findings)), and `mypy` reports no issues. `mypy` needs `infrastructure/__init__.py`; without it, it finds each stack under two module names and stops.

## Codebase map

```
├── app.py                  # Config dataclass, tags, stack wiring, opt-in cdk-nag scan
├── cdk.json                # Tells the CDK CLI how to run app.py
├── requirements.txt        # Pinned CDK libraries
├── requirements-dev.txt    # Adds cdk-nag and mypy
├── infrastructure/
│   ├── __init__.py         # Makes the stacks a package (mypy needs it)
│   ├── vpc_stack.py        # VPC, subnet tiers, flow logs, Lambda security group, alarm topic
│   ├── database_stack.py   # RDS, DB security group, secret + rotation, DB alarms
│   ├── compute_stack.py    # API Gateway, Lambda, IAM grants, API/Lambda alarms
│   └── monitoring_stack.py # Cross-stack CloudWatch dashboard
└── runtime/
    └── handler.py          # Lambda handler (no boto3)
```

Dependencies flow one way: `vpc → database → compute → monitoring`. A stack may consume constructs from stacks to its left, never to its right. Reversing this creates CloudFormation export cycles.

## Ground rules

These apply to every file.

- **Written for two audiences.** This is a demonstration of AWS architecture. Each file needs to be understandable by both corporate decision-makers and software engineers. Comment above and inline in non-technical language, and code simply and with obvious intent.
- **Config lives in one place.** Sizes, counts, retention and removal policy come from the frozen `DemoConfig` dataclass in `app.py`. Stacks receive it as a keyword-only `config` argument. No hardcoded sizing inside stacks.
- **Pass constructs, not strings.** Stacks exchange typed interfaces (`ec2.IVpc`, `ec2.ISecurityGroup`, `secretsmanager.ISecret`), never ARN or ID strings. CDK generates the exports and grants for you.
- **Grants over policy statements.** Use `resource.grant_*()` and `connections`/security-group rules. Write a raw `iam.PolicyStatement` only when no grant method exists, and never with `"*"` resources you control.
- **A security group lives in the stack of the resource it protects.** A group that only identifies callers and has no inbound rules, like the Lambda security group, lives in `vpc_stack.py` so the database and compute stacks can both use it. See the [database gotcha](#gotcha-why-the-db-security-group-is-not-in-vpc_stackpy).
- **Demo defaults are deliberate and reversible.** `RemovalPolicy.DESTROY`, 1-week logs, one NAT gateway, single-AZ RDS, no deletion protection. Production flips these via `DemoConfig`, not by editing stacks.
- **No boto3 in `infrastructure/`.** CDK resolves references at synth time; there is nothing to call. Use CDK context lookups (`ec2.Vpc.from_lookup`, `ssm.StringParameter.value_from_lookup`) for existing resources; the CDK CLI performs and caches them in `cdk.context.json`.
- **No asyncio in stacks.** CDK synthesis is synchronous and deterministic. Async belongs in `runtime/` if anywhere.
- **Pin versions** in `requirements.txt` (see [Dependencies](#dependencies)).

## app.py

**Pillars:** Operational Excellence (tags, single config), Cost Optimization (demo sizing), Security (opt-in cdk-nag scan).

```python
#!/usr/bin/env python3
"""
Secure Serverless API

Brings the network, database, application and monitoring tiers together
in one deployment. Every size, limit and retention period lives in
DemoConfig below, so moving to production means changing settings, not
rewriting stacks.
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
    """Every size, limit, retention period and removal policy.

    One instance describes one environment, such as the demo.
    """

    # Naming
    env_name: str = "demo"
    project: str = "cdk-demo"

    # Network: two AWS locations and a single shared NAT gateway to keep
    # costs down
    vpc_cidr: str = "10.0.0.0/16"
    subnet_cidr_mask: int = 24
    max_azs: int = 2
    nat_gateways: int = 1

    # Database: smallest Graviton size, one location, short backup
    # history
    db_instance_type: ec2.InstanceType = ec2.InstanceType.of(
        ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO
    )
    db_multi_az: bool = False
    db_storage_gib: int = 20
    db_max_storage_gib: int = 50
    db_backup_days: int = 1
    db_deletion_protection: bool = False
    # The database alone can keep a final backup (SNAPSHOT) when it is
    # deleted.
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
    # API Gateway's own logs need an account-wide role shared by every
    # API in the account and Region, so the demo leaves them off.
    api_logging: bool = False

    # Alerts and dashboard
    alarm_email: str | None = None
    metric_period_minutes: int = 5
    alarm_evaluation_periods: int = 1
    db_cpu_alarm_percent: int = 80
    db_cpu_alarm_periods: int = 3
    db_free_storage_alarm_gib: int = 2
    lambda_error_alarm_count: int = 5
    api_5xx_alarm_count: int = 5

    # Housekeeping: what happens to logs and other resources when stacks
    # are deleted
    log_retention: logs.RetentionDays = logs.RetentionDays.ONE_WEEK
    removal_policy: cdk.RemovalPolicy = cdk.RemovalPolicy.DESTROY

    def __post_init__(self) -> None:
        """Reject settings that CloudFormation would refuse."""
        if self.removal_policy == cdk.RemovalPolicy.SNAPSHOT:
            raise ValueError(
                "removal_policy cannot be SNAPSHOT: log groups only "
                "support DESTROY or RETAIN. Use db_removal_policy for a "
                "final database snapshot."
            )


app = cdk.App()
# Choices made at deploy time, for example:
#   cdk deploy --all -c alarm_email=you@example.com -c api_logging=true
config = DemoConfig(
    alarm_email=app.node.try_get_context("alarm_email"),
    api_logging=app.node.try_get_context("api_logging") in (True, "true"),
)

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

    # Findings accepted for the demo, each with its reason and
    # production fix (see "Accepted demo findings" in
    # docs/cdk-well-architected.md). New ones still fail.
    managed_policy = (
        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/"
    )
    lambda_logging = managed_policy + "AWSLambdaBasicExecutionRole"
    NagSuppressions.add_stack_suppressions(
        database,
        [
            NagPackSuppression(
                id="AwsSolutions-RDS3",
                reason="Single-AZ demo; set db_multi_az=True",
            ),
            NagPackSuppression(
                id="AwsSolutions-RDS10",
                reason=(
                    "No deletion protection in the demo; "
                    "set db_deletion_protection"
                ),
            ),
            NagPackSuppression(
                id="AwsSolutions-RDS11",
                reason="Default port 5432; optional to change",
            ),
        ],
    )
    NagSuppressions.add_stack_suppressions(
        compute,
        [
            NagPackSuppression(
                id="AwsSolutions-APIG2",
                reason="No request validation; add per route",
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
                reason=(
                    "AWS managed policies for Lambda, VPC and "
                    "API Gateway logging"
                ),
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
    # API Gateway's own logs are off unless api_logging is on (they
    # need an account-wide role), so accept those findings only then.
    if not config.api_logging:
        NagSuppressions.add_stack_suppressions(
            compute,
            [
                NagPackSuppression(
                    id="AwsSolutions-APIG1",
                    reason="API access logs off in the demo; set api_logging",
                ),
                NagPackSuppression(
                    id="AwsSolutions-APIG6",
                    reason="API error logs off in the demo; set api_logging",
                ),
            ],
        )
    # Only the health check is open; any other route without sign-in
    # still fails.
    NagSuppressions.add_resource_suppressions(
        compute.health_method,
        [
            NagPackSuppression(
                id="AwsSolutions-APIG4",
                reason=(
                    "Open for uptime monitors; returns only ok or unavailable"
                ),
            ),
        ],
    )

app.synth()
```

The app deploys to the account and Region of your current AWS CLI profile (`CDK_DEFAULT_ACCOUNT` and `CDK_DEFAULT_REGION`), so there is no account ID to edit. Pass the alert email at deploy time with `-c alarm_email=you@example.com`.

**Expansion hooks**

- Add a `prod_config = DemoConfig(env_name="prod", db_multi_az=True, nat_gateways=2, removal_policy=cdk.RemovalPolicy.RETAIN, ...)` and select it with `app.node.try_get_context("env")`.
- Add custom tags (e.g. `CostCenter`) as a `dict[str, str]` field on `DemoConfig` and loop over it.

## infrastructure/vpc_stack.py

**Pillars:** Reliability (2 AZs), Security (isolated data tier, flow logs), Cost Optimization (1 NAT gateway).

```python
"""
Secure Network Foundation

A ready-to-deploy network that keeps your sensitive systems off the
public internet.

Built to stay online
The network spans two AWS locations (Availability Zones), so
applications deployed across both can keep serving customers if one
location has an outage.

Three zones, each with one job
A public zone holds only the NAT gateway, the application's way out to
AWS services. The application zone can reach out, but nothing can reach
in. The data zone has no path to or from the internet at all.

Security you can audit
Blocked connection attempts are recorded for security reviews, and every
stack sends its alerts to one shared channel, which emails the team once
you set an address at deploy time.
"""

from typing import TYPE_CHECKING, Any

from aws_cdk import Annotations, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig


class VpcStack(Stack):
    """VPC across two locations with public, app and data subnets."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: "DemoConfig",
        **kwargs: Any,
    ) -> None:
        """Create the network; settings come from ``config``."""
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(config.vpc_cidr),
            max_azs=config.max_azs,
            # One shared NAT gateway keeps demo costs down.
            nat_gateways=config.nat_gateways,
            subnet_configuration=[
                # Holds only the NAT gateway.
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=config.subnet_cidr_mask,
                ),
                # Runs the application: it can reach AWS services, but
                # nothing can reach in.
                ec2.SubnetConfiguration(
                    name="App",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=config.subnet_cidr_mask,
                ),
                # Holds the database, with no route to or from the
                # internet.
                ec2.SubnetConfiguration(
                    name="Data",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=config.subnet_cidr_mask,
                ),
            ],
        )

        # Blocked connection attempts are recorded for security reviews.
        # Logging only blocked traffic keeps log volume and cost low.
        flow_log_group = logs.LogGroup(
            self,
            "FlowLogs",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )
        self.vpc.add_flow_log(
            "FlowLog",
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                flow_log_group
            ),
            traffic_type=ec2.FlowLogTrafficType.REJECT,
        )

        # The application's security group. The database admits it by
        # identity, not by address. It lives here because the database
        # and application stacks both use it.
        self.lambda_sg = ec2.SecurityGroup(
            self,
            "LambdaSg",
            vpc=self.vpc,
            description="Lambda functions; outbound only",
            allow_all_outbound=True,
        )

        # One alert channel for every stack, so warnings reach the team
        # in one place.
        self.alarm_topic = sns.Topic(self, "AlarmTopic", enforce_ssl=True)
        if config.alarm_email is not None:
            self.alarm_topic.add_subscription(
                subscriptions.EmailSubscription(config.alarm_email)
            )
        else:
            # Shown at synth and deploy time, so nobody ships silent
            # alarms by accident.
            Annotations.of(self).add_warning(
                "No alarm_email set: alarms go to a topic nobody is "
                "subscribed to. "
                "Deploy with -c alarm_email=you@example.com."
            )
```

Three subnet tiers: `Public` (NAT gateway only), `App` (Lambda; outbound via NAT), `Data` (RDS; no route to the internet). Flow logs capture rejected traffic only to keep log volume and cost low.

**Expansion hooks**

- In production, set `nat_gateways` to the same value as `max_azs` in `DemoConfig`, so losing one AZ doesn't cut outbound traffic for the others.
- Add a Secrets Manager interface endpoint (`self.vpc.add_interface_endpoint("SecretsEndpoint", service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER)`) so secret reads skip the NAT. Costs per AZ per hour; worth it once traffic is steady.
- Switch flow logs to `FlowLogTrafficType.ALL` when debugging connectivity.

## infrastructure/database_stack.py

**Pillars:** Security (encryption, generated + rotated credentials, isolated subnets), Reliability (backups, alarms, optional Multi-AZ), Performance (gp3, optional Performance Insights), Sustainability (Graviton `t4g`).

```python
"""
Data Tier

A private, encrypted PostgreSQL database that only your application can
reach.

No passwords in code
AWS generates the database login, stores it in Secrets Manager and
changes it automatically on a schedule.

Private by design
The database sits in the data zone with no route to or from the
internet. Only the application and the password-rotation function are
allowed to connect.

Watched around the clock
Alarms warn the team (by email, once an address is set at deploy time)
when the database is working too hard or running low on storage. Its
logs live in a log group this stack owns, so they follow the same
retention and clean-up settings as every other log.
"""

from typing import TYPE_CHECKING, Any

from aws_cdk import Duration, Names, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig


class DatabaseStack(Stack):
    """RDS PostgreSQL with a rotating password and baseline alarms."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: "DemoConfig",
        vpc: ec2.IVpc,
        lambda_security_group: ec2.ISecurityGroup,
        alarm_topic: sns.ITopic,
        **kwargs: Any,
    ) -> None:
        """Create the database in ``vpc``.

        ``lambda_security_group`` may connect and alarms go to
        ``alarm_topic``; every size and retention period comes from
        ``config``.
        """
        super().__init__(scope, construct_id, **kwargs)

        # The database's security group lives with the database.
        # Password rotation opens access on the database's port, and
        # keeping both here avoids a circular dependency between stacks.
        self.db_sg = ec2.SecurityGroup(
            self,
            "DbSg",
            vpc=vpc,
            description="RDS; inbound from app and rotation functions only",
            allow_all_outbound=False,
        )

        # Database logs go to a log group this stack owns, so they
        # follow the same retention and clean-up settings as every other
        # log. A fixed database name lets the group exist before the
        # database starts writing to it. (A change that would replace
        # the database then needs a new name first.)
        instance_id = f"{config.project}-{config.env_name}-postgres"
        db_logs = logs.LogGroup(
            self,
            "PostgresLogs",
            log_group_name=f"/aws/rds/instance/{instance_id}/postgresql",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )

        self.instance = rds.DatabaseInstance(
            self,
            "Postgres",
            instance_identifier=instance_id,
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            # Efficient AWS Graviton processors.
            instance_type=config.db_instance_type,
            vpc=vpc,
            # Kept in the data zone, with no route to or from the
            # internet.
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.db_sg],
            # No passwords in code: AWS generates the login and stores
            # it in Secrets Manager.
            credentials=rds.Credentials.from_generated_secret("app_admin"),
            multi_az=config.db_multi_az,
            # Storage starts small and grows automatically up to a set
            # limit.
            allocated_storage=config.db_storage_gib,
            max_allocated_storage=config.db_max_storage_gib,
            storage_type=rds.StorageType.GP3,
            # Your data is encrypted at rest.
            storage_encrypted=True,
            backup_retention=Duration.days(config.db_backup_days),
            deletion_protection=config.db_deletion_protection,
            # Demo: deleted with the stack. Production: SNAPSHOT keeps a
            # final backup.
            removal_policy=config.db_removal_policy,
            # Database logs go to CloudWatch for troubleshooting and
            # audits.
            cloudwatch_logs_exports=["postgresql"],
            enable_performance_insights=config.db_performance_insights,
        )

        self.instance.node.add_dependency(db_logs)

        # Only the application may connect, on the database's own port.
        self.instance.connections.allow_default_port_from(
            lambda_security_group, "Postgres from Lambda"
        )

        secret = self.instance.secret
        if secret is None:
            raise ValueError("Expected RDS to generate a credentials secret")
        self.secret: secretsmanager.ISecret = secret

        # The password changes automatically on a schedule. The rotation
        # function runs in the application zone so it can reach Secrets
        # Manager through the NAT gateway.
        rotation = self.instance.add_rotation_single_user(
            automatically_after=Duration.days(config.secret_rotation_days),
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # The rotation function's logs also live in a group this stack
        # owns. CDK names the function after the rotation's unique ID,
        # so the group can exist first.
        rotation_logs = logs.LogGroup(
            self,
            "RotationLogs",
            log_group_name=f"/aws/lambda/{Names.unique_id(rotation)}",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )
        rotation.node.add_dependency(rotation_logs)

        # Alarms send warnings to the shared alert channel.
        alarm_action = cloudwatch_actions.SnsAction(alarm_topic)
        period = Duration.minutes(config.metric_period_minutes)
        cpu_minutes = (
            config.metric_period_minutes * config.db_cpu_alarm_periods
        )

        cpu_alarm = cloudwatch.Alarm(
            self,
            "DbCpuHigh",
            metric=self.instance.metric_cpu_utilization(period=period),
            threshold=config.db_cpu_alarm_percent,
            evaluation_periods=config.db_cpu_alarm_periods,
            alarm_description=(
                f"Database CPU at {config.db_cpu_alarm_percent}% or more "
                f"for {cpu_minutes} minutes"
            ),
        )
        cpu_alarm.add_alarm_action(alarm_action)

        storage_alarm = cloudwatch.Alarm(
            self,
            "DbStorageLow",
            metric=self.instance.metric_free_storage_space(period=period),
            threshold=config.db_free_storage_alarm_gib * 1024**3,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            evaluation_periods=config.alarm_evaluation_periods,
            alarm_description=(
                f"Less than {config.db_free_storage_alarm_gib} GiB "
                "free database storage"
            ),
        )
        storage_alarm.add_alarm_action(alarm_action)
```

### Gotcha: why the DB security group is not in vpc_stack.py

`add_rotation_single_user()` opens the DB security group to the rotation Lambda on the database's port. That port is a token owned by `DatabaseStack`. If the DB security group lived in `VpcStack`, the VPC stack would depend on the database stack, which already depends on the VPC stack, and synth fails with `DependencyCycle`. Keeping the DB security group with the database avoids this. The Lambda security group stays in `VpcStack` because both downstream stacks consume it.

**Expansion hooks**

- Production: `db_multi_az=True`, `db_deletion_protection=True`, `db_backup_days=7` or more, `db_removal_policy=RemovalPolicy.SNAPSHOT`.
- Add an `rds.DatabaseProxy` between Lambda and RDS when concurrency grows; it pools connections so Lambda bursts don't exhaust `max_connections`.
- Swap `storage_encrypted=True` for `storage_encryption_key=kms.Key(...)` to use a customer-managed key.

## infrastructure/compute_stack.py

**Pillars:** Security (least-privilege grant, VPC placement, throttling), Reliability (reserved concurrency protects the DB, alarms), Operational Excellence (X-Ray, access logs), Sustainability and Cost (ARM64/Graviton Lambda).

```python
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

from aws_cdk import Annotations, Duration, Stack
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

        if config.api_iam_auth:
            # Only AWS identities you approve through IAM can call the
            # API.
            authorization = apigw.AuthorizationType.IAM
        else:
            authorization = apigw.AuthorizationType.NONE

        # API Gateway's own logs (access and error logs) need an
        # account-wide logging role shared by every API in this account
        # and Region. The demo leaves them off so it changes nothing
        # outside itself; turn them on with -c api_logging=true.
        logging_level = apigw.MethodLoggingLevel.OFF
        access_log_destination: apigw.IAccessLogDestination | None = None
        access_log_format: apigw.AccessLogFormat | None = None
        if config.api_logging:
            # Every API request is logged with its time, address, route
            # and result.
            access_logs = logs.LogGroup(
                self,
                "ApiAccessLogs",
                retention=config.log_retention,
                removal_policy=config.removal_policy,
            )
            logging_level = apigw.MethodLoggingLevel.ERROR
            access_log_destination = apigw.LogGroupLogDestination(access_logs)
            access_log_format = (
                apigw.AccessLogFormat.json_with_standard_fields(
                    caller=False,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=False,
                )
            )
        else:
            Annotations.of(self).add_info(
                "API Gateway logging is off, so the demo changes no "
                "account-wide settings. Turn it on with "
                "-c api_logging=true."
            )

        self.api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=self.handler,
            # Only the routes declared below exist; API Gateway rejects
            # everything else.
            proxy=False,
            # With API logging on, CDK creates the account-wide logging
            # role. It is kept on teardown (CDK's default) because other
            # APIs in the account and Region may rely on it.
            cloud_watch_role=config.api_logging,
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
                logging_level=logging_level,
                access_log_destination=access_log_destination,
                access_log_format=access_log_format,
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

        if config.api_logging:
            # API Gateway's own error logs go to a group this stack
            # owns, created before the stage starts writing, so they
            # follow the same retention and clean-up.
            execution_logs = logs.LogGroup(
                self,
                "ApiExecutionLogs",
                log_group_name=(
                    "API-Gateway-Execution-Logs_"
                    f"{self.api.rest_api_id}/{config.env_name}"
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
```

Notes:

- `reserved_concurrent_executions` caps how many Lambdas can hold DB connections at once. Size it below the RDS instance's connection limit. The throttle alarm tells you when the cap is hit.
- `db_secret.grant_read()` produces a policy scoped to that one secret ARN.
- `params_and_secrets` attaches the AWS Parameters and Secrets Lambda Extension, which is how the handler reads the secret without boto3.
- `proxy=False` means only the declared routes exist: `GET /items`, which requires IAM authorization (`api_iam_auth`, on by default), and `GET /health`, open so uptime monitors can reach it. Routes added with `self.api.root.add_resource(...)` inherit IAM authorization.
- `api_logging` (off by default) controls API Gateway's own access and error logs. They need an account-wide logging role shared by every API in the account and Region, so the demo leaves them off and changes nothing outside itself. When it is on, `cloud_watch_role` creates that role and CDK keeps it on teardown (its default), so other APIs are never left without logging.

**Expansion hooks**

- IAM authorization already covers every route except `/health`. Add a Cognito or Lambda authorizer only if end customers sign in.
- Attach AWS WAF (`wafv2.CfnWebACLAssociation`) to the stage for rate-based and managed rule protection.
- Add request models and validators once routes accept bodies or parameters.
- If you don't need REST API features (usage plans, request validation, WAF), `aws_apigatewayv2.HttpApi` is cheaper and lower latency.

## infrastructure/monitoring_stack.py

**Pillars:** Operational Excellence (one cross-stack view of API, Lambda and RDS health).

```python
"""
Operations Dashboard

One screen that shows the health of the API, the application and the
database side by side, so anyone can see at a glance how the service is
doing.
"""

from typing import TYPE_CHECKING, Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_rds as rds
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig


class MonitoringStack(Stack):
    """Cross-stack CloudWatch dashboard.

    Alarms live with the resources they watch, in the other stacks.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: "DemoConfig",
        database: rds.IDatabaseInstance,
        handler: lambda_.IFunction,
        api: apigw.RestApi,
        **kwargs: Any,
    ) -> None:
        """Create a dashboard for ``api``, ``handler``, ``database``."""
        super().__init__(scope, construct_id, **kwargs)

        period = Duration.minutes(config.metric_period_minutes)

        dashboard = cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name=f"{config.project}-{config.env_name}",
        )

        # Front door: traffic arriving, how fast it is answered, and how
        # much fails.
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="API requests and errors",
                left=[
                    api.metric_count(period=period),
                    api.metric_client_error(period=period),
                    api.metric_server_error(period=period),
                ],
            ),
            cloudwatch.GraphWidget(
                title="API response time (ms)",
                left=[api.metric_latency(period=period)],
            ),
        )

        # Application: work done, failures, and how close it runs to its
        # cap.
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Lambda invocations, errors and throttles",
                left=[
                    handler.metric_invocations(period=period),
                    handler.metric_errors(period=period),
                    handler.metric_throttles(period=period),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Lambda copies running at once",
                left=[
                    handler.metric(
                        "ConcurrentExecutions",
                        period=period,
                        statistic="Maximum",
                    )
                ],
                left_annotations=[
                    cloudwatch.HorizontalAnnotation(
                        value=config.lambda_reserved_concurrency, label="Cap"
                    )
                ],
            ),
        )

        # Database: how hard it is working, connections held, and space
        # left.
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Database CPU (%)",
                left=[database.metric_cpu_utilization(period=period)],
            ),
            cloudwatch.GraphWidget(
                title="Database connections",
                left=[database.metric_database_connections(period=period)],
            ),
            cloudwatch.GraphWidget(
                title="Database free storage (bytes)",
                left=[database.metric_free_storage_space(period=period)],
            ),
        )
```

## runtime/handler.py

**Pillars:** Security (no credentials in env vars or code), Performance (extension caches the secret).

```python
"""
Application Health Check

Confirms that the application can fetch its database login and reach the
database, then reports only "ok" or "unavailable". Login details and
error messages stay in your private logs.

No extra code libraries
AWS's Parameters and Secrets extension fetches the login and caches it
briefly, so the handler needs only Python's standard library.
"""

import json
import logging
import os
import socket
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# The extension answers on this local address and keeps a short-lived
# copy of the login.
SECRETS_ENDPOINT = "http://localhost:2773/secretsmanager/get?secretId="

# Short waits keep every answer well inside the function's time limit.
EXTENSION_TIMEOUT_SECONDS = 3
DATABASE_TIMEOUT_SECONDS = 3


def database_address() -> tuple[str, int]:
    """Return the database host and port from the login secret."""
    secret_id = urllib.parse.quote(os.environ["DB_SECRET_ARN"], safe="")
    request = urllib.request.Request(
        SECRETS_ENDPOINT + secret_id,
        headers={
            "X-Aws-Parameters-Secrets-Token": os.environ["AWS_SESSION_TOKEN"]
        },
    )
    with urllib.request.urlopen(
        request, timeout=EXTENSION_TIMEOUT_SECONDS
    ) as response:
        payload = json.loads(response.read())
    secret = json.loads(payload["SecretString"])
    return secret["host"], int(secret["port"])


def respond(status_code: int, body: dict[str, str]) -> dict[str, Any]:
    """Build an API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Report login and database health for GET /health and /items."""
    try:
        # Opening a connection proves the private network path to the
        # database works.
        with socket.create_connection(
            database_address(), timeout=DATABASE_TIMEOUT_SECONDS
        ):
            pass
    except (OSError, KeyError, ValueError):
        logger.exception("Health check failed")
        return respond(503, {"status": "unavailable"})

    return respond(200, {"status": "ok"})
```

**No boto3.** The extension exposes a local HTTP endpoint on port 2773; the handler authenticates with the function's own session token. The extension caches the secret for 5 minutes (`secrets_manager_ttl` in `compute_stack.py`).

**Rotation caveat.** After a rotation, the cached secret can be stale for up to the TTL. When you add a real DB connection, catch the authentication error, refetch once with `&versionStage=AWSCURRENT` appended to the URL, and retry.

**DB driver packaging.** Lambda runtimes don't include a PostgreSQL driver. Use pure-Python `pg8000` (avoids compiled binaries on ARM64), listed in a new `runtime/requirements.txt`, and bundle it:

```python
from aws_cdk import BundlingOptions

code=lambda_.Code.from_asset(
    "runtime",
    bundling=BundlingOptions(
        image=lambda_.Runtime.PYTHON_3_14.bundling_image,
        platform="linux/arm64",
        command=["bash", "-c", "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"],
    ),
),
```

Bundling requires Docker on the machine running `cdk synth`. (This snippet was not synth-tested; the rest of the doc was.)

## Dependencies

`requirements.txt` (pin exact versions for deterministic synth):

```
aws-cdk-lib==2.272.0
constructs==10.8.1
```

`requirements-dev.txt`:

```
-r requirements.txt
cdk-nag==2.38.2
mypy==2.3.1
```

`cdk-nag` 3.0.2 failed at synth against `aws-cdk-lib` 2.272.0 (`aspect.visit is not a function`); stay on 2.x until that's resolved.

`runtime/requirements.txt` (only once you add a database driver; it doesn't exist yet):

```
pg8000==1.31.5
```

## Pillar checklist

| Pillar                 | Where it's applied                                           |
| ---------------------- | ------------------------------------------------------------ |
| Operational Excellence | App-level tags, `DemoConfig`, X-Ray on API + Lambda, optional API access and error logs (`api_logging`), VPC flow logs, alarms in each stack |
| Security               | Isolated data subnets, SG-to-SG rules only, encrypted RDS, generated + 30-day rotated secret, scoped `grant_read`, API throttling, no credentials in code |
| Reliability            | 2 AZs, 1-day automated backups, reserved concurrency to protect DB, Multi-AZ flag, error/throttle/5xx/CPU/storage alarms |
| Performance Efficiency | gp3 storage with autoscaling to 50 GiB, Performance Insights switch (off on `t4g.micro`, which doesn't support it), secret caching, configurable Lambda memory |
| Cost Optimization      | One NAT gateway, `t4g.micro`, 1-week log retention, `DESTROY` removal policy, REJECT-only flow logs |
| Sustainability         | Graviton for both Lambda (ARM64) and RDS (`t4g`), serverless compute with no idle instances |

## Accepted demo findings

Result of `cdk synth -c nag=1`. Each is intentional for a demo; the right column is the production fix.

| cdk-nag rule | Why it's accepted in demo                                    | Production fix                                          |
| ------------ | ------------------------------------------------------------ | ------------------------------------------------------- |
| APIG2        | No request validation                                        | Add models and validators per route                     |
| APIG1, APIG6 | API Gateway's own logs are off (they need an account-wide role) | Set `api_logging=True`; suppressed only while it's off |
| APIG3        | No WAF                                                       | Associate a WAFv2 web ACL                               |
| APIG4        | `GET /health` is open by design for uptime monitors          | Suppressed on that route only; every other route uses IAM |
| COG4         | Routes use IAM authorization, not Cognito                    | Cognito authorizer only if end customers sign in        |
| IAM4         | AWS managed policies on the Lambda role (logging, VPC access), plus the API Gateway logging role when `api_logging` is on | Generated by CDK; suppressed with justification |
| IAM5         | `Resource: *` from X-Ray                                     | Required by X-Ray; suppressed with justification        |
| RDS3         | Single-AZ                                                    | `db_multi_az=True`                                      |
| RDS10        | Deletion protection off                                      | `db_deletion_protection=True`                           |
| RDS11        | Default port 5432                                            | Optional; set `port=` (the DB security group rule follows the port automatically) |

Accepted findings are suppressed in `app.py`, each with a written `reason`, so any new finding still fails the scan. Most are stack-wide; APIG4 is suppressed only on `GET /health`, and the IAM4/IAM5 suppressions name the exact managed policies and wildcard.

## Validation commands

```bash
cdk synth                 # must succeed with no errors
cdk synth -c nag=1        # review any finding not in the table above
mypy --ignore-missing-imports app.py infrastructure runtime
```

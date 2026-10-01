# CDK Well-Architected Guide

-All code herein in both an example of well-architected code AND a draft of the final codebase. 
-This is a preliminary guide and it may have errors, sometimes serious errors. Do not simply copy code without evaluating it for errors or omissions first. 
-Use documentation from aws.com, especially well architected documentation and CDK documentation. 
-Always notify the user of errors or omissions that you find in this draft. Create new code if you find an error or an omission and justify your changes to the user. 

This codebase applies the six pillars of the AWS Well-Architected Framework. Every snippet below is a draft version of the file it names. Edit the real file, then ask the user to evaluate changes. If the user approves changes, keep this doc in sync with the changes.

Verified with `aws-cdk-lib==2.272.0`, `constructs==10.8.1`: `cdk synth` passes, `mypy` reports no issues, and remaining `cdk-nag` findings are listed under [Accepted demo findings](#accepted-demo-findings).

## Codebase map

```
├── app.py                  # Config dataclass, tags, stack wiring
├── infrastructure/
│   ├── vpc_stack.py        # VPC, subnet tiers, flow logs, Lambda security group
│   ├── database_stack.py   # RDS, DB security group, secret + rotation, DB alarms
│   └── compute_stack.py    # API Gateway, Lambda, IAM grants, API/Lambda alarms
└── runtime/
    └── handler.py          # Lambda handler (no boto3)
```

Dependencies flow one way: `vpc → database → compute`. A stack may consume constructs from stacks to its left, never to its right. Reversing this creates CloudFormation export cycles.

## Ground rules

These apply to every file.

- **Config lives in one place.** Sizes, counts, retention and removal policy come from the frozen `DemoConfig` dataclass in `app.py`. Stacks receive it as a keyword-only `config` argument. No hardcoded sizing inside stacks.
- **Pass constructs, not strings.** Stacks exchange typed interfaces (`ec2.IVpc`, `ec2.ISecurityGroup`, `secretsmanager.ISecret`), never ARN or ID strings. CDK generates the exports and grants for you.
- **Grants over policy statements.** Use `resource.grant_*()` and `connections`/security-group rules. Write a raw `iam.PolicyStatement` only when no grant method exists, and never with `"*"` resources you control.
- **A security group lives in the stack of the resource it protects.** See the [database gotcha](#gotcha-why-the-db-security-group-is-not-in-vpc_stackpy).
- **Demo defaults are deliberate and reversible.** `RemovalPolicy.DESTROY`, 1-week logs, one NAT gateway, single-AZ RDS, no deletion protection. Production flips these via `DemoConfig`, not by editing stacks.
- **No boto3 in `infrastructure/`.** CDK resolves references at synth time; there is nothing to call. Use CDK context lookups (`ec2.Vpc.from_lookup`, `ssm.StringParameter.value_from_lookup`) for existing resources; the CDK CLI performs and caches them in `cdk.context.json`.
- **No asyncio in stacks.** CDK synthesis is synchronous and deterministic. Async belongs in `runtime/` if anywhere.
- **Pin versions** in `requirements.txt` (see [Dependencies](#dependencies)).

## app.py

**Pillars:** Operational Excellence (tags, single config), Cost Optimization (demo sizing), Security (opt-in cdk-nag scan).

```python
"""CDK App entry point: wires the VPC, database, and compute stacks together."""
from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs

from infrastructure.compute_stack import ComputeStack
from infrastructure.database_stack import DatabaseStack
from infrastructure.vpc_stack import VpcStack


@dataclass(frozen=True)
class DemoConfig:
    """Environment-specific settings shared by every stack."""

    env_name: str = "demo"
    project: str = "cdk-demo"
    vpc_cidr: str = "10.0.0.0/16"
    max_azs: int = 2
    nat_gateways: int = 1
    db_instance_type: ec2.InstanceType = ec2.InstanceType.of(
        ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO
    )
    db_multi_az: bool = False
    lambda_memory_mb: int = 256
    lambda_reserved_concurrency: int = 10
    log_retention: logs.RetentionDays = logs.RetentionDays.ONE_WEEK
    removal_policy: cdk.RemovalPolicy = cdk.RemovalPolicy.DESTROY


app = cdk.App()
config = DemoConfig()
env = cdk.Environment(account="123456789012", region="us-east-1")

cdk.Tags.of(app).add("Environment", config.env_name)
cdk.Tags.of(app).add("Project", config.project)
cdk.Tags.of(app).add("ManagedBy", "CDK")

network = VpcStack(app, f"{config.project}-vpc", config=config, env=env)
database = DatabaseStack(
    app, f"{config.project}-database",
    config=config, vpc=network.vpc, lambda_security_group=network.lambda_sg, env=env,
)
ComputeStack(
    app, f"{config.project}-compute",
    config=config, vpc=network.vpc, lambda_security_group=network.lambda_sg,
    db_secret=database.secret, env=env,
)

# Opt-in security scan: `cdk synth -c nag=1`
if app.node.try_get_context("nag") is not None:
    from cdk_nag import AwsSolutionsChecks

    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

app.synth()
```

Replace the placeholder `account` with your own, or use `os.environ["CDK_DEFAULT_ACCOUNT"]`.

**Expansion hooks**

- Add a `prod_config = DemoConfig(env_name="prod", db_multi_az=True, nat_gateways=2, removal_policy=cdk.RemovalPolicy.RETAIN, ...)` and select it with `app.node.try_get_context("env")`.
- Add custom tags (e.g. `CostCenter`) as a `dict[str, str]` field on `DemoConfig` and loop over it.

## infrastructure/vpc_stack.py

**Pillars:** Reliability (2 AZs), Security (isolated data tier, flow logs), Cost Optimization (1 NAT gateway).

```python
"""Network layer: VPC, subnet tiers, security groups, and flow logs."""
from typing import TYPE_CHECKING

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig


class VpcStack(Stack):
    """Multi-AZ VPC with public, private-with-egress, and isolated subnet tiers."""

    def __init__(self, scope: Construct, construct_id: str, *, config: "DemoConfig", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self, "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(config.vpc_cidr),
            max_azs=config.max_azs,
            nat_gateways=config.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="App", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
                ec2.SubnetConfiguration(name="Data", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ],
        )

        flow_log_group = logs.LogGroup(
            self, "FlowLogs",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )
        self.vpc.add_flow_log(
            "FlowLog",
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_log_group),
            traffic_type=ec2.FlowLogTrafficType.REJECT,
        )

        self.lambda_sg = ec2.SecurityGroup(
            self, "LambdaSg", vpc=self.vpc,
            description="Lambda functions; outbound only",
            allow_all_outbound=True,
        )
```

Three subnet tiers: `Public` (NAT gateway only), `App` (Lambda; outbound via NAT), `Data` (RDS; no route to the internet). Flow logs capture rejected traffic only to keep log volume and cost low.

**Expansion hooks**

- Set `nat_gateways=config.max_azs` in production so losing one AZ doesn't cut outbound traffic for the others.
- Add a Secrets Manager interface endpoint (`self.vpc.add_interface_endpoint("SecretsEndpoint", service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER)`) so secret reads skip the NAT. Costs per AZ per hour; worth it once traffic is steady.
- Switch flow logs to `FlowLogTrafficType.ALL` when debugging connectivity.

## infrastructure/database_stack.py

**Pillars:** Security (encryption, generated + rotated credentials, isolated subnets), Reliability (backups, alarms, optional Multi-AZ), Performance (gp3, Performance Insights), Sustainability (Graviton `t4g`).

```python
"""Data layer: RDS PostgreSQL instance in isolated subnets."""
from typing import TYPE_CHECKING

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig


class DatabaseStack(Stack):
    """RDS PostgreSQL with generated credentials, encryption, and baseline alarms."""

    def __init__(
        self, scope: Construct, construct_id: str, *,
        config: "DemoConfig", vpc: ec2.IVpc, lambda_security_group: ec2.ISecurityGroup, **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # DB security group lives here, not in VpcStack: rotation adds ingress rules
        # that reference this stack's DB port, which would create a cross-stack cycle.
        self.db_sg = ec2.SecurityGroup(
            self, "DbSg", vpc=vpc,
            description="RDS; inbound from Lambda SG only",
            allow_all_outbound=False,
        )
        self.db_sg.add_ingress_rule(
            peer=lambda_security_group,
            connection=ec2.Port.tcp(5432),
            description="Postgres from Lambda",
        )

        self.instance = rds.DatabaseInstance(
            self, "Postgres",
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            instance_type=config.db_instance_type,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[self.db_sg],
            credentials=rds.Credentials.from_generated_secret("app_admin"),
            multi_az=config.db_multi_az,
            allocated_storage=20,
            max_allocated_storage=50,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            backup_retention=Duration.days(1),
            deletion_protection=False,
            removal_policy=config.removal_policy,
            cloudwatch_logs_exports=["postgresql"],
            cloudwatch_logs_retention=config.log_retention,
            enable_performance_insights=True,
        )

        secret = self.instance.secret
        if secret is None:
            raise ValueError("Expected RDS to generate a credentials secret")
        self.secret: secretsmanager.ISecret = secret

        # Rotation Lambda runs in App subnets so it can reach Secrets Manager via NAT
        self.instance.add_rotation_single_user(
            automatically_after=Duration.days(30),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

        cloudwatch.Alarm(
            self, "DbCpuHigh",
            metric=self.instance.metric_cpu_utilization(period=Duration.minutes(5)),
            threshold=80,
            evaluation_periods=3,
            alarm_description="RDS CPU above 80% for 15 minutes",
        )
        cloudwatch.Alarm(
            self, "DbStorageLow",
            metric=self.instance.metric_free_storage_space(period=Duration.minutes(5)),
            threshold=2 * 1024**3,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            evaluation_periods=1,
            alarm_description="Less than 2 GiB free storage",
        )
```

### Gotcha: why the DB security group is not in vpc_stack.py

`add_rotation_single_user()` opens the DB security group to the rotation Lambda on the database's port. That port is a token owned by `DatabaseStack`. If the DB security group lived in `VpcStack`, the VPC stack would depend on the database stack, which already depends on the VPC stack, and synth fails with `DependencyCycle`. Keeping the DB security group with the database avoids this. The Lambda security group stays in `VpcStack` because both downstream stacks consume it.

**Expansion hooks**

- Production: `db_multi_az=True`, `deletion_protection=True`, `backup_retention=Duration.days(7)` or more, `RemovalPolicy.SNAPSHOT`.
- Add an `rds.DatabaseProxy` between Lambda and RDS when concurrency grows; it pools connections so Lambda bursts don't exhaust `max_connections`.
- Wire alarms to an SNS topic: `alarm.add_alarm_action(cloudwatch_actions.SnsAction(topic))` (import `aws_cloudwatch_actions`).
- Swap `storage_encrypted=True` for `storage_encryption_key=kms.Key(...)` to use a customer-managed key.

## infrastructure/compute_stack.py

**Pillars:** Security (least-privilege grant, VPC placement, throttling), Reliability (reserved concurrency protects the DB, alarms), Operational Excellence (X-Ray, access logs), Sustainability and Cost (ARM64/Graviton Lambda).

```python
"""Compute layer: API Gateway REST API fronting a VPC-attached Lambda."""
from typing import TYPE_CHECKING

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig


class ComputeStack(Stack):
    """REST API -> Lambda -> RDS, with least-privilege IAM and bounded concurrency."""

    def __init__(
        self, scope: Construct, construct_id: str, *,
        config: "DemoConfig", vpc: ec2.IVpc, lambda_security_group: ec2.ISecurityGroup,
        db_secret: secretsmanager.ISecret, **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        fn_log_group = logs.LogGroup(
            self, "HandlerLogs",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )

        self.handler = lambda_.Function(
            self, "Handler",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset("runtime"),
            memory_size=config.lambda_memory_mb,
            timeout=Duration.seconds(15),
            reserved_concurrent_executions=config.lambda_reserved_concurrency,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[lambda_security_group],
            log_group=fn_log_group,
            tracing=lambda_.Tracing.ACTIVE,
            params_and_secrets=lambda_.ParamsAndSecretsLayerVersion.from_version(
                lambda_.ParamsAndSecretsVersions.V1_0_103,
                cache_size=10,
                secrets_manager_ttl=Duration.minutes(5),
            ),
            environment={
                "DB_SECRET_ARN": db_secret.secret_arn,
                "LOG_LEVEL": "INFO",
            },
        )
        db_secret.grant_read(self.handler)

        access_logs = logs.LogGroup(
            self, "ApiAccessLogs",
            retention=config.log_retention,
            removal_policy=config.removal_policy,
        )
        self.api = apigw.LambdaRestApi(
            self, "Api",
            handler=self.handler,
            proxy=False,
            deploy_options=apigw.StageOptions(
                stage_name=config.env_name,
                throttling_rate_limit=50,
                throttling_burst_limit=100,
                tracing_enabled=True,
                logging_level=apigw.MethodLoggingLevel.ERROR,
                access_log_destination=apigw.LogGroupLogDestination(access_logs),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=False, http_method=True, ip=True, protocol=True,
                    request_time=True, resource_path=True, response_length=True,
                    status=True, user=False,
                ),
            ),
        )
        items = self.api.root.add_resource("items")
        items.add_method("GET")

        cloudwatch.Alarm(
            self, "HandlerErrors",
            metric=self.handler.metric_errors(period=Duration.minutes(5)),
            threshold=5,
            evaluation_periods=1,
            alarm_description="Lambda errors > 5 in 5 minutes",
        )
        cloudwatch.Alarm(
            self, "HandlerThrottles",
            metric=self.handler.metric_throttles(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            alarm_description="Lambda hitting reserved concurrency cap",
        )
        cloudwatch.Alarm(
            self, "Api5xx",
            metric=self.api.metric_server_error(period=Duration.minutes(5)),
            threshold=5,
            evaluation_periods=1,
            alarm_description="API 5xx > 5 in 5 minutes",
        )
```

Notes:

- `reserved_concurrent_executions` caps how many Lambdas can hold DB connections at once. Size it below the RDS instance's connection limit. The throttle alarm tells you when the cap is hit.
- `db_secret.grant_read()` produces a policy scoped to that one secret ARN.
- `params_and_secrets` attaches the AWS Parameters and Secrets Lambda Extension, which is how the handler reads the secret without boto3.
- `proxy=False` means only explicitly declared routes exist. Add routes with `self.api.root.add_resource(...)`.

**Expansion hooks**

- Add an authorizer (IAM or Lambda) to each `add_method()` call before exposing real data.
- Attach AWS WAF (`wafv2.CfnWebACLAssociation`) to the stage for rate-based and managed rule protection.
- Add request models and validators once routes accept bodies or parameters.
- If you don't need REST API features (usage plans, request validation, WAF), `aws_apigatewayv2.HttpApi` is cheaper and lower latency.

## runtime/handler.py

**Pillars:** Security (no credentials in env vars or code), Performance (extension caches the secret).

```python
"""Lambda handler. Reads DB credentials via the Parameters and Secrets extension (no boto3)."""
import json
import os
import urllib.parse
import urllib.request

SECRETS_ENDPOINT = "http://localhost:2773/secretsmanager/get?secretId="


def get_db_credentials() -> dict:
    """Fetch and cache-hit the RDS secret through the local extension endpoint."""
    secret_arn = urllib.parse.quote(os.environ["DB_SECRET_ARN"], safe="")
    request = urllib.request.Request(
        SECRETS_ENDPOINT + secret_arn,
        headers={"X-Aws-Parameters-Secrets-Token": os.environ["AWS_SESSION_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.loads(response.read())
    return json.loads(payload["SecretString"])


def handler(event: dict, context: object) -> dict:
    creds = get_db_credentials()
    # Hook: open a DB connection with a bundled driver (e.g. pg8000) using creds["host"], etc.
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"db_host": creds["host"], "status": "ok"}),
    }
```

**No boto3.** The extension exposes a local HTTP endpoint on port 2773; the handler authenticates with the function's own session token. The extension caches the secret for 5 minutes (`secrets_manager_ttl` in `compute_stack.py`).

**Rotation caveat.** After a rotation, the cached secret can be stale for up to the TTL. When you add a real DB connection, catch the authentication error, refetch once with `&versionStage=AWSCURRENT` appended to the URL, and retry.

**DB driver packaging.** Lambda runtimes don't include a PostgreSQL driver. Use pure-Python `pg8000` (avoids compiled binaries on ARM64), listed in `runtime/requirements.txt`, and bundle it:

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
cdk-nag==2.38.2
mypy
```

`cdk-nag` 3.0.2 failed at synth against `aws-cdk-lib` 2.272.0 (`aspect.visit is not a function`); stay on 2.x until that's resolved.

`runtime/requirements.txt`:

```
pg8000==1.31.5
```

## Pillar checklist

| Pillar                 | Where it's applied                                           |
| ---------------------- | ------------------------------------------------------------ |
| Operational Excellence | App-level tags, `DemoConfig`, X-Ray on API + Lambda, API access logs, VPC flow logs, alarms in each stack |
| Security               | Isolated data subnets, SG-to-SG rules only, encrypted RDS, generated + 30-day rotated secret, scoped `grant_read`, API throttling, no credentials in code |
| Reliability            | 2 AZs, 1-day automated backups, reserved concurrency to protect DB, Multi-AZ flag, error/throttle/5xx/CPU/storage alarms |
| Performance Efficiency | gp3 storage with autoscaling to 50 GiB, Performance Insights, secret caching, configurable Lambda memory |
| Cost Optimization      | One NAT gateway, `t4g.micro`, 1-week log retention, `DESTROY` removal policy, REJECT-only flow logs |
| Sustainability         | Graviton for both Lambda (ARM64) and RDS (`t4g`), serverless compute with no idle instances |

## Accepted demo findings

Result of `cdk synth -c nag=1`. Each is intentional for a demo; the right column is the production fix.

| cdk-nag rule | Why it's accepted in demo                                    | Production fix                                          |
| ------------ | ------------------------------------------------------------ | ------------------------------------------------------- |
| APIG2        | No request validation                                        | Add models and validators per route                     |
| APIG3        | No WAF                                                       | Associate a WAFv2 web ACL                               |
| APIG4, COG4  | No authorizer                                                | Cognito, IAM, or Lambda authorizer                      |
| IAM4         | AWS managed policies on Lambda and API Gateway logging roles | Generated by CDK; suppress with justification           |
| IAM5         | `Resource: *` from X-Ray and VPC ENI permissions             | Required by those services; suppress with justification |
| RDS3         | Single-AZ                                                    | `db_multi_az=True`                                      |
| RDS10        | Deletion protection off                                      | `deletion_protection=True`                              |
| RDS11        | Default port 5432                                            | Optional; set `port=` and update the SG rule            |

Suppress accepted findings with `NagSuppressions.add_stack_suppressions(stack, [...])` and a written `reason` so the scan stays useful.

## Validation commands

```bash
cdk synth                 # must succeed with no errors
cdk synth -c nag=1        # review any finding not in the table above
mypy --ignore-missing-imports app.py infrastructure runtime
```

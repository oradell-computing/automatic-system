"""
Data Tier

A private, encrypted PostgreSQL database that only your application can reach.

No passwords in code
AWS generates the database login, stores it in Secrets Manager and changes it
automatically on a schedule.

Private by design
The database sits in the data zone with no route to or from the internet. Only
the application and the password-rotation function are allowed to connect.

Watched around the clock
Alarms warn the team when the database is working too hard or running low on
storage.
"""

from typing import TYPE_CHECKING, Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from constructs import Construct

if TYPE_CHECKING:
    from app import DemoConfig


class DatabaseStack(Stack):
    """RDS PostgreSQL with a generated, rotating password and baseline alarms."""

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
        """Create the database in ``vpc``, reachable from ``lambda_security_group``.

        Alarms go to ``alarm_topic``; every size and retention period comes from
        ``config``.
        """
        super().__init__(scope, construct_id, **kwargs)

        # The database's security group lives with the database. Password rotation
        # opens access on the database's port, and keeping both here avoids a
        # circular dependency between stacks.
        self.db_sg = ec2.SecurityGroup(
            self,
            "DbSg",
            vpc=vpc,
            description="RDS; inbound from the app and rotation functions only",
            allow_all_outbound=False,
        )

        self.instance = rds.DatabaseInstance(
            self,
            "Postgres",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            # Efficient AWS Graviton processors.
            instance_type=config.db_instance_type,
            vpc=vpc,
            # Kept in the data zone, with no route to or from the internet.
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.db_sg],
            # No passwords in code: AWS generates the login and stores it in
            # Secrets Manager.
            credentials=rds.Credentials.from_generated_secret("app_admin"),
            multi_az=config.db_multi_az,
            # Storage starts small and grows automatically up to a set limit.
            allocated_storage=config.db_storage_gib,
            max_allocated_storage=config.db_max_storage_gib,
            storage_type=rds.StorageType.GP3,
            # Your data is encrypted at rest.
            storage_encrypted=True,
            backup_retention=Duration.days(config.db_backup_days),
            deletion_protection=config.db_deletion_protection,
            # Demo: deleted with the stack. Production: SNAPSHOT keeps a final backup.
            removal_policy=config.db_removal_policy,
            # Database logs go to CloudWatch for troubleshooting and audits.
            cloudwatch_logs_exports=["postgresql"],
            cloudwatch_logs_retention=config.log_retention,
            enable_performance_insights=config.db_performance_insights,
        )

        # Only the application may connect, on the database's own port.
        self.instance.connections.allow_default_port_from(
            lambda_security_group, "Postgres from Lambda"
        )

        secret = self.instance.secret
        if secret is None:
            raise ValueError("Expected RDS to generate a credentials secret")
        self.secret: secretsmanager.ISecret = secret

        # The password changes automatically on a schedule. The rotation function
        # runs in the application zone so it can reach Secrets Manager through the
        # NAT gateway.
        self.instance.add_rotation_single_user(
            automatically_after=Duration.days(config.secret_rotation_days),
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # Alarms send warnings to the shared alert channel.
        alarm_action = cloudwatch_actions.SnsAction(alarm_topic)
        period = Duration.minutes(config.metric_period_minutes)
        cpu_minutes = config.metric_period_minutes * config.db_cpu_alarm_periods

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

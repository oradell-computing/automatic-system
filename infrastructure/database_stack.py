"""Data tier: a private, encrypted PostgreSQL database that only your application can reach."""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_rds as rds,
)
from constructs import Construct


class DatabaseStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        base_internal_sg: ec2.ISecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Locked down by default: the database cannot reach out to the internet or other systems.
        self.db_security_group = ec2.SecurityGroup(
            self,
            "DatabaseSecurityGroup",
            vpc=vpc,
            description="Database access, open only to the internal application tier",
            allow_all_outbound=False,
        )

        self.database = rds.DatabaseInstance(
            self,
            "CoreDatabaseInstance",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_15
            ),
            vpc=vpc,
            # Kept off the internet: private subnets with no public route.
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.db_security_group],
            # No passwords in code: AWS generates the login and stores it safely in Secrets Manager.
            credentials=rds.Credentials.from_generated_secret("app_admin"),
            # Lower running costs with efficient AWS Graviton processors (one-line switch to x86).
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON,
                ec2.InstanceSize.MICRO,
            ),
            # Starts at 20 GB and grows automatically to 50 GB, so you pay only for what you need.
            allocated_storage=20,
            max_allocated_storage=50,
            # Your data is encrypted at rest, supporting your compliance requirements.
            storage_encrypted=True,
            # Demo settings: the database is removed with the stack.
            # For production, use RemovalPolicy.RETAIN and enable deletion protection.
            removal_policy=RemovalPolicy.DESTROY,
            deletion_protection=False,
        )
        self.db_credentials = self.database.secret

        # Only the internal application tier may connect, and the console shows exactly why.
        self.database.connections.allow_default_port_from(
            base_internal_sg,
            "Allow inbound PostgreSQL traffic from internal compute security group",
        )

        # Publishes the database address so other services can connect with ease.
        CfnOutput(
            self,
            "DatabaseEndpointAddress",
            value=self.database.instance_endpoint.hostname,
            export_name="CoreDbEndpoint",
        )
        # Publishes where the login is stored (never the password) for secure, on-demand access.
        CfnOutput(
            self,
            "DatabaseSecretArn",
            value=self.db_credentials.secret_arn,
            export_name="CoreDbSecretArn",
        )

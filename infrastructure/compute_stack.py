"""
Application Tier

A serverless API that answers customer requests, with no servers to manage.

Pay only for what you use
Your code runs only when a request arrives, so you pay for compute only while
it is working. Traffic limits protect your application and database from
sudden spikes.

Private by design
The application runs inside your private network, next to the database, and
connects to it without ever crossing the public internet.

Least-privilege access
Beyond basic logging and networking, the application can read exactly one
thing: its database login. That login is fetched securely at run time and is
never stored in code.
"""

import os

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigw,
    aws_ec2 as ec2,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

dirname = os.path.dirname(__file__)


class ComputeStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        base_internal_sg: ec2.ISecurityGroup,
        db_secret: secretsmanager.ISecret,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # A private connection to AWS Secrets Manager: the database login never travels over the public internet.
        ec2.InterfaceVpcEndpoint(
            self,
            "SecretsManagerEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
        )

        # Application logs are kept for 30 days.
        # Demo setting: logs are removed with the stack. For production, use RemovalPolicy.RETAIN.
        log_group = logs.LogGroup(
            self,
            "ApiHandlerLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.function = lambda_.Function(
            self,
            "ApiHandler",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=lambda_.Code.from_asset(os.path.join(dirname, "..", "runtime")),
            # Lower running costs with efficient AWS Graviton processors.
            architecture=lambda_.Architecture.ARM_64,
            timeout=Duration.seconds(10),
            log_group=log_group,
            # Runs in the private data zone beside the database, with no internet exposure.
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            # The shared internal security group is the only one the database accepts.
            security_groups=[base_internal_sg],
            # Only the location of the login is shared with the application, never the password.
            environment={"DB_SECRET_ARN": db_secret.secret_arn},
        )

        # The application may read its database login, and nothing else.
        db_secret.grant_read(self.function)

        # The public front door. Traffic limits keep the application and database steady during spikes.
        self.api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=self.function,
            # Self-contained: this stack changes nothing outside your application.
            cloud_watch_role=False,
            deploy_options=apigw.StageOptions(
                throttling_rate_limit=10,
                throttling_burst_limit=20,
            ),
        )

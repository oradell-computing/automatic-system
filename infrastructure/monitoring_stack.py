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

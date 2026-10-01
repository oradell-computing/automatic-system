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

"""
Secure Network Foundation

A ready-to-deploy network that keeps your sensitive systems off the public
internet, without the ongoing cost of NAT gateways.

Built to stay online
The network spans two independent AWS locations (Availability Zones), so
applications deployed across both can keep serving customers if one location
has an outage.

Sensitive data stays private
A public-facing zone handles incoming customer traffic. A private data zone has
no path to or from the internet, which greatly reduces exposure for databases
and internal systems. When those systems need AWS services, private connections
can be added so that traffic never touches the public internet.

Leaner running costs
Without NAT gateways, the private zone avoids the recurring hourly and
per-gigabyte charges they carry.

Clear visibility into network activity
Network traffic is logged and kept for 30 days, giving your security and
compliance teams a record for investigations and audits.

Secure by default for new services
Teams building on this foundation get a ready-made security group that blocks
all incoming connections, a safe starting point for every new service.
"""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_logs as logs,
)
from constructs import Construct


class VpcStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Spans two AWS locations for resilience, with a public zone for customers and a private zone for data.
        self.vpc = ec2.Vpc(
            self,
            "CoreVpc",
            max_azs=2,
            ip_addresses=ec2.IpAddresses.cidr("10.100.0.0/16"),
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="IngressPublic",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                # No route to the internet, and no NAT charges.
                ec2.SubnetConfiguration(
                    name="DataIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
            nat_gateways=0,
        )

        # Network activity is logged and kept for 30 days for investigations and audits.
        log_group = logs.LogGroup(
            self,
            "VpcFlowLogsGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            # Demo setting: logs are removed with the stack. For production, use RemovalPolicy.RETAIN.
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.vpc.add_flow_log(
            "FlowLog",
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(log_group),
        )

        # A safe starting point for new services: no incoming connections until you allow them.
        self.internal_sg = ec2.SecurityGroup(
            self,
            "BaseInternalSecurityGroup",
            vpc=self.vpc,
            description="Default locked security baseline: deny inbound, allow outbound",
            allow_all_outbound=True,
        )

        # Shared with other stacks so the data and compute layers can build on this network.
        CfnOutput(
            self,
            "VpcIdExport",
            value=self.vpc.vpc_id,
            export_name="CoreVpcId",
        )
        CfnOutput(
            self,
            "BaseSgExport",
            value=self.internal_sg.security_group_id,
            export_name="CoreBaseSgId",
        )

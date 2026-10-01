# CLAUDE.md

AWS CDK v2 demo infrastructure in Python. Build infrastructure only unless told otherwise.

## Layout
- `app.py`: `DemoConfig` dataclass, tags, stack wiring
- `infrastructure/vpc_stack.py`: VPC, subnets, flow logs, Lambda security group, shared alarm topic
- `infrastructure/database_stack.py`: RDS, DB security group, secret rotation, DB alarms
- `infrastructure/compute_stack.py`: API Gateway, Lambda, IAM grants, API/Lambda alarms
- `infrastructure/monitoring_stack.py`: Cross-stack CloudWatch dashboard only
- `runtime/handler.py`: Lambda handler

## Before editing
Before changing a file in `infrastructure/` or `runtime/`, read its section in `docs/cdk-well-architected.md`. Update the equivalent code in that section (and only the code) when the file's design changes.

## Rules
- Stack dependencies flow `vpc → database → compute → monitoring`, never the reverse. CloudFormation rejects cycles whatever their purpose.
- Each stack owns its own log groups and per-resource alarms.
- Shared alerting targets (SNS topics, log buckets) go in `vpc_stack.py`, the foundation every other stack builds on, so any stack (including the network) can use them. Cross-stack dashboards go in `monitoring_stack.py`.
- All sizing, retention, and removal policies come from `DemoConfig`. No hardcoded values in stacks.
- Pass typed constructs between stacks (`IVpc`, `ISecret`, `sns.Topic`), not ARN strings.
- Use `grant_*()` methods over raw `PolicyStatement`. Never write a `"*"` resource yourself; the wildcards AWS requires (X-Ray, VPC networking) come from CDK and are accepted scan findings.
- A security group lives in the same stack as the resource it protects. A group that only identifies callers and has no inbound rules, like the Lambda security group, lives in `vpc_stack.py` so the database and compute stacks can both use it.
- No boto3. If it seems necessary, stop and tell me why.
- No asyncio in stacks.
- Pin exact versions in `requirements.txt`.

## Code style
- PEP 8 (79-character lines; 72 for comments and docstrings), 257, 484, 526, 557: type hints, docstrings, dataclasses.
- Plain, readable code. No design patterns or boilerplate for their own sake.
- Compare optional values to `None` explicitly.

## Commands
- `cdk synth`: must pass after every change
- `cdk synth -c nag=1`: security scan
- `mypy --ignore-missing-imports app.py infrastructure runtime`

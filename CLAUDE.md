# CLAUDE.md

AWS CDK v2 demo infrastructure in Python. Build infrastructure only unless told otherwise.

## Layout
- `app.py`: `DemoConfig` dataclass, tags, stack wiring
- `infrastructure/vpc_stack.py`: VPC, subnets, flow logs, Lambda security group
- `infrastructure/database_stack.py`: RDS, DB security group, secret rotation
- `infrastructure/compute_stack.py`: API Gateway, Lambda, IAM grants
- `runtime/handler.py`: Lambda handler

## Before editing
Read `docs/cdk-well-architected.md` before changing any file in `infrastructure/` or `runtime/`. Keep that doc in sync with code changes.

## Rules
- Stack dependencies flow `vpc → database → compute`, never the reverse. CloudFormation rejects cycles whatever their purpose.
- Shared logging/alerting targets (SNS topics, log buckets) go in the earliest stack that needs them. Cross-stack dashboards go in a `monitoring_stack.py` after `compute`.
- All sizing, retention, and removal policies come from `DemoConfig`. No hardcoded values in stacks.
- Pass typed constructs between stacks (`IVpc`, `ISecret`), not ARN strings.
- Use `grant_*()` methods over raw `PolicyStatement`. No `"*"` resources.
- A security group lives in the same stack as the resource it protects.
- No boto3. If it seems necessary, stop and tell me why.
- No asyncio in stacks.
- Pin exact versions in `requirements.txt`.

## Code style
- PEP 8, 257, 484, 526, 557: type hints, docstrings, dataclasses.
- Plain, readable code. No design patterns or boilerplate for their own sake.
- Compare optional values to `None` explicitly.

## Commands
- `cdk synth`: must pass after every change
- `cdk synth -c nag=1`: security scan
- `mypy --ignore-missing-imports app.py infrastructure runtime`

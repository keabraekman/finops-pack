# finops-pack

Lightweight AWS FinOps CLI scaffold with cross-account role assumption and optional Cost Optimization Hub enrollment.

## Setup

```bash
# install uv first if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# create env + install dev dependencies
uv sync --dev
```

## Local commands

```bash
uv run ruff check .
uv run ruff format .
uv run mypy .
uv run pytest
uv run finops-pack demo
```

## AWS setup

Deploy the cross-account role into the target AWS account:

```bash
aws cloudformation deploy \
  --template-file cfn/readhonly-role.yaml \
  --stack-name finops-pack-readonly \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    TrustedAccountId=111122223333 \
    ExternalId=replace-me
```

If you also want `finops-pack` to opt that account into Cost Optimization Hub, add:

```bash
AllowCostOptimizationHubEnrollment=true
```

That optional policy grants `cost-optimization-hub:UpdateEnrollmentStatus` plus the IAM permissions AWS requires to create the `AWSServiceRoleForCostOptimizationHub` service-linked role.

## Running against AWS

You can pass settings on the CLI or in `config.yaml`. See `config.example.yaml` for the supported keys.

```bash
uv run finops-pack run \
  --role-arn arn:aws:iam::123456789012:role/finops-pack-readonly \
  --external-id replace-me \
  --region us-east-1 \
  --check-identity
```

## Optional: enable Cost Optimization Hub

`--enable-coh` is off by default. When you pass it, `finops-pack` calls `UpdateEnrollmentStatus(status=Active)` after assuming the target role.

```bash
uv run finops-pack run \
  --role-arn arn:aws:iam::123456789012:role/finops-pack-readonly \
  --external-id replace-me \
  --enable-coh
```

AWS automatically creates the `AWSServiceRoleForCostOptimizationHub` service-linked role when enrollment is enabled. AWS also notes that imported recommendations are stored in `us-east-1` and can take up to 24 hours to appear.

## How to revoke access

Delete the CloudFormation stack to remove the cross-account role and managed policies created for `finops-pack`:

```bash
aws cloudformation delete-stack --stack-name finops-pack-readonly
aws cloudformation wait stack-delete-complete --stack-name finops-pack-readonly
```

If you previously enabled Cost Optimization Hub, that service-linked role is separate from this stack. To remove it too, first opt out of Cost Optimization Hub, then delete `AWSServiceRoleForCostOptimizationHub` from IAM.

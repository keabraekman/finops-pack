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
uv run finops-pack run --role-arn arn:aws:iam::123456789012:role/finops-pack-readonly --external-id replace-me
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

## Trust policy notes

- `TrustedAccountId` should be the specific service or provider account that runs `finops-pack`, not a broad wildcard trust.
- `ExternalId` is the cross-account confused-deputy safeguard for multi-tenant access. Use a unique value per customer, tenant, or workspace instead of reusing one shared value everywhere.
- AWS does not treat `ExternalId` as a secret because principals that can view the role can also see the condition. Treat it as a unique identifier under your control, not a password.
- Every `AssumeRole` call must pass the exact same `ExternalId` value that was set when the stack was created, or STS denies the request.

## Manual smoke test

If you want to validate the role in a test account or org, use a unique test `ExternalId` and check both the success and failure paths:

```bash
# positive path: matching ExternalId should succeed
uv run finops-pack run \
  --role-arn arn:aws:iam::123456789012:role/finops-pack-readonly \
  --external-id your-test-external-id \
  --check-identity

# negative path: wrong ExternalId should fail
uv run finops-pack run \
  --role-arn arn:aws:iam::123456789012:role/finops-pack-readonly \
  --external-id wrong-external-id \
  --check-identity
```

## Required billing prerequisites

- Use the AWS Organizations management account when you need organization-wide billing visibility. AWS Billing and Cost Management gives the management account access to its own charges plus member-account charges, while member accounts only see their own cost and usage data.
- Cost Optimization Hub must be opted in before account recommendations appear. `finops-pack run --enable-coh` can perform the single-account opt-in path if the target role includes the optional COH permissions.
- Successful runs include an access report that best-effort checks COH enrollment, Cost Explorer readiness, and Cost Explorer resource-level daily data readiness. Modules with missing prerequisites are marked `DEGRADED` with the reason surfaced in both CLI output and the dashboard.

## Known limits

- Cost Explorer resource-level daily data is opt-in and must be enabled in Billing and Cost Management preferences before you can query it.
- Cost Explorer resource-level daily data only covers the last 14 days.
- AWS Organizations inventory collection requires `organizations:ListAccounts` in the account where `finops-pack` runs.

## IAM policy templates

Starter IAM templates live in `iam/`:

- `iam/policy-min.json` is the baseline starting point.
- `iam/policy-full.json` adds the optional Cost Optimization Hub enrollment permissions.

The `iam-policy` CLI is a stub today. It emits one of those bundled templates and will later narrow actions based on enabled finops-pack modules.

```bash
uv run finops-pack iam-policy --mode min
uv run finops-pack iam-policy --mode full --output /tmp/finops-pack-policy.json
```

## Minimal permissions today

Based on the commands currently implemented in this repo, the narrowest target-role permissions are:

- No identity-policy permissions are required just to assume the role and run `--check-identity`; the gate is the trust policy, and `sts:GetCallerIdentity` is permissionless.
- If you use `--enable-coh`, add only these extra permissions:
  - `cost-optimization-hub:UpdateEnrollmentStatus`
  - `iam:CreateServiceLinkedRole` for `cost-optimization-hub.bcm.amazonaws.com`
  - `iam:PutRolePolicy` on `AWSServiceRoleForCostOptimizationHub`
- Baseline read access now also includes `cost-optimization-hub:ListEnrollmentStatuses`, `cost-optimization-hub:ListRecommendationSummaries`, and `cost-optimization-hub:ListRecommendations` so finops-pack can report COH readiness and snapshot raw COH data.

The checked-in CloudFormation template and starter IAM JSON files are still broader because they are scaffolding for future collectors and billing reads.

## Running against AWS

You can pass settings on the CLI or in `config.yaml`. See `config.example.yaml` for the supported keys.

`regions` is an optional fixed region coverage list. If you set it, include the primary `region` in that list. finops-pack reports this as `region_discovery_strategy=fixed` and carries the list into `access_report.json` and the dashboard.

```bash
uv run finops-pack run \
  --role-arn arn:aws:iam::123456789012:role/finops-pack-readonly \
  --external-id replace-me \
  --region us-east-1 \
  --check-identity \
  --output-dir output
```

Successful runs now write:

- `output/accounts.json`: normalized account inventory plus prod/nonprod classification metadata
- `output/access_report.json`: region coverage, best-effort prerequisite checks, and module readiness
- `out/raw/coh_summaries.json`: raw `ListRecommendationSummaries` pages plus flattened items and deduped savings total
- `out/raw/coh_recommendations.json`: raw `ListRecommendations` pages plus flattened items
- `output/dashboard.html`: HTML dashboard with an Account Map section

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

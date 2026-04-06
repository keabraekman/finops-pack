# Permissions Explained

This document explains what the `finops-pack` target role can read, why `ExternalId` matters,
what data ends up in an operator-managed S3 bucket, and how to remove the access later.

## Baseline read permissions

The narrowest built-in policy for current default behavior is `infra/aws/iam/policy-min.json`.

It allows:

- `organizations:ListAccounts` so the tool can inventory accounts from the management account.
- `ec2:DescribeInstances` so the non-prod schedule estimator can inventory EC2 instances.
- `ce:GetCostAndUsage` for the last-30-days spend baseline and Cost Explorer readiness checks.
- `ce:GetCostAndUsageWithResources` for resource-level daily cost readiness and schedule estimates.
- `cost-optimization-hub:ListEnrollmentStatuses` so the prerequisite check can tell whether COH is enabled.
- `cost-optimization-hub:ListRecommendationSummaries` and `cost-optimization-hub:ListRecommendations` for raw COH snapshots.
- `cost-optimization-hub:GetRecommendation` for the richer top-opportunity detail that powers `exports.json` and the dashboard.

## Optional permissions

`infra/aws/iam/policy-full.json` adds optional capabilities that are disabled by default in the CLI:

- `ce:GetRightsizingRecommendation`
- `ce:GetSavingsPlansPurchaseRecommendation`
- `ce:GetSavingsPlanPurchaseRecommendationDetails`
- `ce:StartSavingsPlansPurchaseRecommendationGeneration`
- `cost-optimization-hub:UpdateEnrollmentStatus`
- `iam:CreateServiceLinkedRole` and `iam:PutRolePolicy` for the AWS-managed Cost Optimization Hub service-linked role

## Report bucket permissions

If you publish reports to S3, add bucket-scoped access separately instead of broad S3 read/write:

- `s3:ListBucket` on the report bucket
- `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` on the specific client prefix

Use a dedicated bucket or a tightly-scoped prefix per customer or workspace. A sample private-only
bucket policy lives in `infra/aws/iam/private-report-bucket-policy.json`.

## ExternalId rationale

`ExternalId` is the confused-deputy safeguard for cross-account access.

Why it matters:

- It prevents a third party from reusing a trusted role ARN without also knowing the exact identifier your operator account expects.
- It should be unique per customer, tenant, or workspace.
- It is not a password. IAM principals that can inspect the role can also see the condition value.
- The protection comes from uniqueness and exact matching, not secrecy.

Operational guidance:

- Generate a new `ExternalId` per client or workspace.
- Store it in your operator system of record.
- Pass the same value on every `sts:AssumeRole` call.
- Rotate it by updating the CloudFormation stack and your operator config together.

## Threat model notes

When you enable report publishing, `finops-pack` stores report artifacts in an operator-managed
bucket. Think about that bucket as sensitive operational data, not generic static hosting.

The main risks are:

- A mis-scoped bucket or prefix leaks recommendation details, account identifiers, or report history to the wrong tenant.
- A long-lived presigned URL can leak outside its intended audience.
- A stale report prefix can linger longer than the customer expects.
- Raw recommendations may contain account IDs, resource IDs, regions, and recommended target states.

Recommended controls:

- Keep the bucket private and enable S3 Block Public Access.
- Use a dedicated prefix per client: `s3://bucket/<client-id>/<run-id>/`.
- Keep retention short. The default report retention is 7 days.
- Use server-side encryption at rest, preferably SSE-KMS if you already operate KMS controls. SSE-S3 is the minimum baseline.
- Use presigned URLs only for sharing and let them expire quickly.
- Restrict operator principals to the exact bucket and client prefixes they need.
- Treat `summary.json`, `exports.json`, and the HTML report as customer data.

## Encryption at rest

The bucket policy example enforces server-side encryption on uploads. You can choose:

- `AES256` for SSE-S3 when you want the simplest default.
- `aws:kms` when you want explicit key control, audit trails, or separation by operator environment.

If you use SSE-KMS, update the bucket policy and the caller permissions to match your KMS key.

## How to remove access

To remove the cross-account read role:

```bash
aws cloudformation delete-stack --stack-name finops-pack-readonly
aws cloudformation wait stack-delete-complete --stack-name finops-pack-readonly
```

If you also enabled Cost Optimization Hub enrollment from the role:

1. Opt the account out of Cost Optimization Hub.
2. Delete the `AWSServiceRoleForCostOptimizationHub` service-linked role if you no longer need it.

If you used S3 report publishing:

1. Delete the client prefixes or the whole report bucket.
2. Remove the bucket policy exception for the operator role.
3. Revoke any stored operator credentials that were allowed to publish or read those reports.

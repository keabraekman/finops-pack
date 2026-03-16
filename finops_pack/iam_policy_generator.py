"""Starter IAM policy templates and a simple generator stub."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

PolicyMode = Literal["min", "full"]

BASE_POLICY_STATEMENTS: list[dict[str, Any]] = [
    {
        "Sid": "ReadInventoryAndUsage",
        "Effect": "Allow",
        "Action": [
            "autoscaling:Describe*",
            "cloudwatch:Describe*",
            "cloudwatch:Get*",
            "cloudwatch:List*",
            "ec2:Describe*",
            "elasticloadbalancing:Describe*",
            "lambda:Get*",
            "lambda:List*",
            "organizations:ListAccounts",
            "rds:Describe*",
            "rds:ListTagsForResource",
            "s3:GetBucketLocation",
            "s3:GetBucketTagging",
            "s3:GetBucketVersioning",
            "s3:GetEncryptionConfiguration",
            "s3:GetLifecycleConfiguration",
            "s3:GetMetricsConfiguration",
            "s3:GetObject",
            "s3:GetObjectTagging",
            "s3:List*",
            "tag:GetResources",
            "tag:GetTagKeys",
            "tag:GetTagValues",
        ],
        "Resource": "*",
    },
    {
        "Sid": "ReadCostAndBillingSignals",
        "Effect": "Allow",
        "Action": [
            "ce:Describe*",
            "ce:Get*",
            "ce:List*",
            "cur:DescribeReportDefinitions",
            "pricing:DescribeServices",
            "pricing:GetAttributeValues",
            "pricing:GetProducts",
        ],
        "Resource": "*",
    },
]

OPTIONAL_FEATURE_STATEMENTS: list[dict[str, Any]] = [
    {
        "Sid": "AllowCreateCostOptimizationHubServiceLinkedRole",
        "Effect": "Allow",
        "Action": ["iam:CreateServiceLinkedRole"],
        "Resource": (
            "arn:aws:iam::*:role/aws-service-role/"
            "cost-optimization-hub.bcm.amazonaws.com/AWSServiceRoleForCostOptimizationHub"
        ),
        "Condition": {
            "StringLike": {"iam:AWSServiceName": "cost-optimization-hub.bcm.amazonaws.com"}
        },
    },
    {
        "Sid": "AllowPutCostOptimizationHubRolePolicy",
        "Effect": "Allow",
        "Action": ["iam:PutRolePolicy"],
        "Resource": (
            "arn:aws:iam::*:role/aws-service-role/"
            "cost-optimization-hub.bcm.amazonaws.com/AWSServiceRoleForCostOptimizationHub"
        ),
    },
    {
        "Sid": "AllowUpdateCostOptimizationHubEnrollment",
        "Effect": "Allow",
        "Action": ["cost-optimization-hub:UpdateEnrollmentStatus"],
        "Resource": "*",
    },
]


def generate_policy(mode: PolicyMode = "min") -> dict[str, Any]:
    """
    Return a starter IAM policy template.

    Today this is a small stub that selects between bundled baseline templates.
    Later it can narrow permissions based on enabled finops-pack modules.
    """
    statements = deepcopy(BASE_POLICY_STATEMENTS)
    if mode == "full":
        statements.extend(deepcopy(OPTIONAL_FEATURE_STATEMENTS))
    return {"Version": "2012-10-17", "Statement": statements}


def render_policy(mode: PolicyMode = "min") -> str:
    """Render a starter IAM policy template as formatted JSON."""
    return json.dumps(generate_policy(mode), indent=2) + "\n"


def write_policy(mode: PolicyMode, output_path: str | Path) -> Path:
    """Write a starter IAM policy template to disk."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_policy(mode), encoding="utf-8")
    return path


def policy_template_path(mode: PolicyMode) -> Path:
    """Return the repo path for the checked-in policy template snapshot."""
    return Path(__file__).resolve().parents[1] / "iam" / f"policy-{mode}.json"

# ruff: noqa: E501
"""Native Lambda memory rightsizing analysis."""

from __future__ import annotations

from typing import Any

from finops_pack.analysis.action_builders import build_grouped_action
from finops_pack.analysis.pricing import estimate_lambda_monthly_cost
from finops_pack.domain.models import NormalizedRecommendation

MEMORY_STEPS = [128, 256, 512, 1024, 1536, 2048, 3008, 4096, 5120, 6144, 7168, 8192, 10240]


def _covered_resource_ids(recommendations: list[NormalizedRecommendation] | None) -> set[str]:
    return {
        str(rec.resource_id)
        for rec in recommendations or []
        if rec.resource_id and rec.current_resource_type == "LambdaFunction"
    }


def _lower_memory(memory_mb: int) -> int | None:
    if memory_mb not in MEMORY_STEPS:
        return None
    index = MEMORY_STEPS.index(memory_mb)
    if index <= 1:
        return None
    return MEMORY_STEPS[index - 1]


def build_lambda_memory_actions(
    inventory_snapshot: dict[str, Any] | None,
    *,
    recommendations: list[NormalizedRecommendation] | None = None,
) -> list:
    """Build native Lambda memory rightsizing opportunities."""
    if not isinstance(inventory_snapshot, dict):
        return []
    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    covered_ids = _covered_resource_ids(recommendations)
    candidates: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        function_arn = str(item.get("functionArn") or "")
        if not function_arn or function_arn in covered_ids:
            continue
        memory_mb = int(item.get("memorySize") or 0)
        lower_memory = _lower_memory(memory_mb)
        avg_duration_ms = item.get("avgDurationMs14d")
        monthly_invocations = item.get("monthlyInvocations14d")
        monthly_errors = float(item.get("monthlyErrors14d") or 0.0)
        if (
            lower_memory is None
            or not isinstance(avg_duration_ms, (int, float))
            or not isinstance(monthly_invocations, (int, float))
            or float(monthly_invocations) <= 0
            or monthly_errors > 0
        ):
            continue
        current_monthly_cost = estimate_lambda_monthly_cost(
            memory_mb=memory_mb,
            monthly_invocations=float(monthly_invocations),
            average_duration_ms=float(avg_duration_ms),
        )
        lower_monthly_cost = estimate_lambda_monthly_cost(
            memory_mb=lower_memory,
            monthly_invocations=float(monthly_invocations),
            average_duration_ms=float(avg_duration_ms),
        )
        monthly_savings = round(current_monthly_cost - lower_monthly_cost, 2)
        if monthly_savings <= 2:
            continue
        candidates.append(
            {
                "account_name": str(item.get("accountName") or item.get("accountId") or "Unknown"),
                "account_id": str(item.get("accountId") or "Unknown"),
                "region": str(item.get("region") or ""),
                "resource_name": str(item.get("functionName") or function_arn),
                "resource_id": function_arn,
                "detail": (
                    f"{memory_mb} MB -> {lower_memory} MB · avg duration {float(avg_duration_ms):.1f} ms "
                    f"· invocations {int(float(monthly_invocations))}"
                ),
                "monthly_savings": monthly_savings,
            }
        )

    if not candidates:
        return []

    return [
        build_grouped_action(
            bucket="Rightsize",
            lever_key="lambda_memory_rightsizing",
            action_label=f"Rightsize {len(candidates)} Lambda function{'s' if len(candidates) != 1 else ''}",
            source_label="Native finops-pack",
            items=sorted(
                candidates, key=lambda item: (-float(item["monthly_savings"]), item["resource_id"])
            ),
            risk="low",
            effort="low",
            confidence="medium",
            why_it_matters="Over-allocated Lambda memory raises the cost of every invocation even when the function rarely needs it.",
            what_to_do_first="Lower memory on one lower-risk function and validate latency before applying the same step elsewhere.",
            evidence_summary=f"{len(candidates)} Lambda function(s) had enough stable traffic and headroom to support a conservative one-step memory reduction.",
        )
    ]

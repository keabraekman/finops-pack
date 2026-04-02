from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass


def build_stable_finding_id(*, resource_id: str, finding_type: str, region: str) -> str:
    """Build a stable finding ID from the fields that identify the same issue over time."""
    digest = hashlib.sha256(f"{resource_id}|{finding_type}|{region}".encode()).hexdigest()
    return f"finding-{digest}"


@dataclass(config=ConfigDict(extra="forbid"))
class SavingsRange:
    monthly_low_usd: float = Field(ge=0)
    monthly_high_usd: float = Field(ge=0)
    annual_low_usd: float | None = Field(default=None, ge=0)
    annual_high_usd: float | None = Field(default=None, ge=0)

    def __post_init__(self) -> None:
        if self.monthly_high_usd < self.monthly_low_usd:
            raise ValueError("monthly_high_usd must be >= monthly_low_usd")

        if self.annual_low_usd is None:
            self.annual_low_usd = round(self.monthly_low_usd * 12, 2)

        if self.annual_high_usd is None:
            self.annual_high_usd = round(self.monthly_high_usd * 12, 2)

        if self.annual_high_usd < self.annual_low_usd:
            raise ValueError("annual_high_usd must be >= annual_low_usd")


ActionBucket = Literal[
    "Stop waste",
    "Rightsize",
    "Buy discounts",
    "Storage cleanup",
]
ActionPriority = Literal["low", "medium", "high"]
ActionSourceLabel = Literal["Native finops-pack", "AWS COH", "CE fallback"]


@dataclass(config=ConfigDict(extra="forbid"))
class Resource:
    provider: Literal["aws"]
    account_id: str
    region: str
    service: str
    resource_id: str
    resource_name: str | None = None
    arn: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


@dataclass(config=ConfigDict(extra="forbid"))
class AccountRecord:
    account_id: str
    name: str
    email: str | None = None
    status: str = "ACTIVE"


@dataclass(config=ConfigDict(extra="forbid"))
class AccountMapEntry:
    account_id: str
    name: str
    email: str | None = None
    status: str = "ACTIVE"
    environment: Literal["prod", "nonprod", "unknown"] = "unknown"
    confidence: Literal["high", "medium", "low"] = "low"
    classification_source: Literal["config", "regex", "default"] = "default"
    classification_reason: str = "No classification rule matched."


@dataclass(config=ConfigDict(extra="forbid"))
class RegionCoverage:
    strategy: Literal["fixed"] = "fixed"
    primary_region: str = "us-east-1"
    regions: list[str] = Field(default_factory=list)


@dataclass(config=ConfigDict(extra="forbid"))
class AccessCheck:
    check_id: str
    label: str
    status: Literal["ACTIVE", "DEGRADED"] = "DEGRADED"
    enabled: bool | None = None
    reason: str = ""
    checked_in_region: str = "us-east-1"


@dataclass(config=ConfigDict(extra="forbid"))
class ModuleStatus:
    module_id: str
    label: str
    status: Literal["ACTIVE", "DEGRADED"] = "DEGRADED"
    reason: str = ""


@dataclass(config=ConfigDict(extra="forbid"))
class AccessReport:
    account_id: str | None = None
    region_coverage: RegionCoverage | None = None
    checks: list[AccessCheck] = Field(default_factory=list)
    modules: list[ModuleStatus] = Field(default_factory=list)


@dataclass(config=ConfigDict(extra="forbid"))
class SpendBaselineBucket:
    start: str
    end: str
    amount: float = Field(ge=0)
    unit: str = "USD"


@dataclass(config=ConfigDict(extra="forbid"))
class SpendBaseline:
    window_start: str
    window_end: str
    window_days: int = Field(ge=1)
    granularity: Literal["MONTHLY"] = "MONTHLY"
    metric: Literal["UnblendedCost"] = "UnblendedCost"
    total_amount: float = Field(ge=0)
    average_daily_amount: float = Field(ge=0)
    unit: str = "USD"
    monthly_buckets: list[SpendBaselineBucket] = Field(default_factory=list)


@dataclass(config=ConfigDict(extra="forbid"))
class DailyCostPoint:
    date: str
    amount: float = Field(ge=0)


@dataclass(config=ConfigDict(extra="forbid"))
class ResourceCostSeries:
    identifier: str
    unit: str = "USD"
    total_amount: float = Field(default=0, ge=0)
    daily_costs: list[DailyCostPoint] = Field(default_factory=list)


@dataclass(config=ConfigDict(extra="forbid"))
class Recommendation:
    code: str
    title: str
    summary: str
    action: str
    effort: Literal["low", "medium", "high"] = "low"
    risk: Literal["low", "medium", "high"] = "low"
    savings: SavingsRange | None = None


@dataclass(config=ConfigDict(extra="forbid"))
class ActionOpportunity:
    bucket: ActionBucket
    action_label: str
    monthly_savings: float = Field(ge=0)
    risk: ActionPriority = "low"
    effort: ActionPriority = "low"
    confidence: ActionPriority = "medium"
    source_label: ActionSourceLabel = "Native finops-pack"
    why_it_matters: str = ""
    what_to_do_first: str = ""
    evidence_summary: str = ""
    action_id: str | None = None
    opportunity_count: int = Field(default=1, ge=1)
    account_names: list[str] = Field(default_factory=list)
    supporting_items: list[dict[str, Any]] = Field(default_factory=list)

    def __post_init__(self) -> None:
        if self.action_id is None:
            digest = hashlib.sha256(
                (
                    f"{self.bucket}|{self.action_label}|{self.source_label}|"
                    f"{self.opportunity_count}"
                ).encode()
            ).hexdigest()
            self.action_id = f"action-{digest}"


@dataclass(config=ConfigDict(extra="forbid"))
class NormalizedRecommendation:
    recommendation_id: str
    source: Literal["cost_optimization_hub"] = "cost_optimization_hub"
    category: Literal[
        "rightsizing / idle deletion",
        "commitment (SP/RI)",
        "storage/network/etc.",
    ] = "storage/network/etc."
    account_id: str | None = None
    region: str | None = None
    resource_id: str | None = None
    resource_arn: str | None = None
    current_resource_type: str | None = None
    recommended_resource_type: str | None = None
    current_resource_summary: str | None = None
    recommended_resource_summary: str | None = None
    current_resource_details: dict[str, Any] | None = None
    recommended_resource_details: dict[str, Any] | None = None
    action_type: str | None = None
    currency_code: str | None = None
    estimated_monthly_savings: float | None = Field(default=None, ge=0)
    estimated_monthly_cost: float | None = Field(default=None, ge=0)
    estimated_savings_percentage: float | None = None
    recommendation: Recommendation | None = None


@dataclass(config=ConfigDict(extra="forbid"))
class Finding:
    finding_type: str
    severity: Literal["low", "medium", "high", "critical"]
    resource: Resource
    recommendation: Recommendation
    finding_id: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.finding_id is None:
            self.finding_id = build_stable_finding_id(
                resource_id=self.resource.resource_id,
                finding_type=self.finding_type,
                region=self.resource.region,
            )

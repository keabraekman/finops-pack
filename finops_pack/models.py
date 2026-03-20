from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass


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
class Recommendation:
    code: str
    title: str
    summary: str
    action: str
    effort: Literal["low", "medium", "high"] = "low"
    risk: Literal["low", "medium", "high"] = "low"
    savings: SavingsRange | None = None


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
    action_type: str | None = None
    currency_code: str | None = None
    estimated_monthly_savings: float | None = Field(default=None, ge=0)
    estimated_monthly_cost: float | None = Field(default=None, ge=0)
    estimated_savings_percentage: float | None = None
    recommendation: Recommendation | None = None


@dataclass(config=ConfigDict(extra="forbid"))
class Finding:
    finding_id: str
    finding_type: str
    severity: Literal["low", "medium", "high", "critical"]
    resource: Resource
    recommendation: Recommendation
    notes: str | None = None

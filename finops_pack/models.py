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
class Recommendation:
    code: str
    title: str
    summary: str
    action: str
    effort: Literal["low", "medium", "high"] = "low"
    risk: Literal["low", "medium", "high"] = "low"
    savings: SavingsRange | None = None


@dataclass(config=ConfigDict(extra="forbid"))
class Finding:
    finding_id: str
    finding_type: str
    severity: Literal["low", "medium", "high", "critical"]
    resource: Resource
    recommendation: Recommendation
    notes: str | None = None

"""Conservative pricing heuristics used by native analyzers."""

from __future__ import annotations

from typing import Literal

PricingConfidence = Literal["low", "medium", "high"]

EC2_SIZE_UNIT_MAP = {
    "nano": 0.125,
    "micro": 0.25,
    "small": 0.5,
    "medium": 1.0,
    "large": 2.0,
    "xlarge": 4.0,
    "2xlarge": 8.0,
    "3xlarge": 12.0,
    "4xlarge": 16.0,
    "6xlarge": 24.0,
    "8xlarge": 32.0,
    "9xlarge": 36.0,
    "10xlarge": 40.0,
    "12xlarge": 48.0,
    "16xlarge": 64.0,
    "18xlarge": 72.0,
    "24xlarge": 96.0,
    "32xlarge": 128.0,
}
EC2_FAMILY_MULTIPLIER = {
    "t": 0.45,
    "m": 1.0,
    "c": 0.9,
    "r": 1.25,
    "x": 1.55,
    "i": 1.35,
    "d": 1.15,
}
EC2_BASE_HOURLY_RATE = 0.048

RDS_SIZE_UNIT_MAP = {
    "micro": 0.25,
    "small": 0.5,
    "medium": 1.0,
    "large": 2.0,
    "xlarge": 4.0,
    "2xlarge": 8.0,
    "4xlarge": 16.0,
    "8xlarge": 32.0,
    "12xlarge": 48.0,
    "16xlarge": 64.0,
    "24xlarge": 96.0,
    "32xlarge": 128.0,
}
RDS_FAMILY_MULTIPLIER = {
    "t": 0.5,
    "m": 1.0,
    "r": 1.3,
    "x": 1.6,
    "i": 1.4,
    "z": 1.5,
}
RDS_BASE_HOURLY_RATE = 0.08

MONTHLY_HOURS = 730
NAT_GATEWAY_HOURLY_RATE = 0.045
FARGATE_VCPU_HOURLY_RATE = 0.04048
FARGATE_GB_HOURLY_RATE = 0.004445
LAMBDA_REQUEST_RATE_PER_MILLION = 0.20
LAMBDA_GB_SECOND_RATE = 0.0000166667
S3_STANDARD_RATE_PER_GIB_MONTH = 0.023
S3_STANDARD_IA_RATE_PER_GIB_MONTH = 0.0125
S3_GLACIER_IR_RATE_PER_GIB_MONTH = 0.004


def _family_prefix(name: str) -> str:
    for char in name:
        if char.isalpha():
            return char.lower()
    return ""


def estimate_ec2_hourly_cost(instance_type: str) -> tuple[float, PricingConfidence]:
    """Estimate EC2 hourly cost from the instance family and size."""
    normalized = instance_type.strip()
    family, _, size = normalized.partition(".")
    family_prefix = _family_prefix(family)
    size_units = EC2_SIZE_UNIT_MAP.get(size.lower(), 2.0 if size else 2.0)
    multiplier = EC2_FAMILY_MULTIPLIER.get(family_prefix, 1.0)
    confidence: PricingConfidence = (
        "high"
        if family_prefix in EC2_FAMILY_MULTIPLIER and size.lower() in EC2_SIZE_UNIT_MAP
        else "medium"
    )
    return round(EC2_BASE_HOURLY_RATE * size_units * multiplier, 4), confidence


def estimate_rds_hourly_cost(db_instance_class: str) -> tuple[float, PricingConfidence]:
    """Estimate RDS hourly cost from the DB instance class shape."""
    normalized = db_instance_class.removeprefix("db.")
    family, _, size = normalized.partition(".")
    family_prefix = _family_prefix(family)
    size_units = RDS_SIZE_UNIT_MAP.get(size.lower(), 2.0 if size else 2.0)
    multiplier = RDS_FAMILY_MULTIPLIER.get(family_prefix, 1.0)
    confidence: PricingConfidence = (
        "high"
        if family_prefix in RDS_FAMILY_MULTIPLIER and size.lower() in RDS_SIZE_UNIT_MAP
        else "medium"
    )
    return round(RDS_BASE_HOURLY_RATE * size_units * multiplier, 4), confidence


def estimate_fargate_monthly_cost(
    *,
    cpu_units: int,
    memory_mib: int,
    desired_count: int,
) -> float:
    """Estimate monthly Fargate compute cost from service reservations."""
    vcpu = max(cpu_units, 0) / 1024
    memory_gib = max(memory_mib, 0) / 1024
    return round(
        desired_count
        * MONTHLY_HOURS
        * ((vcpu * FARGATE_VCPU_HOURLY_RATE) + (memory_gib * FARGATE_GB_HOURLY_RATE)),
        2,
    )


def estimate_nat_gateway_monthly_cost(count: int = 1) -> float:
    """Return a conservative NAT Gateway monthly base cost estimate."""
    return round(max(count, 0) * NAT_GATEWAY_HOURLY_RATE * MONTHLY_HOURS, 2)


def estimate_lambda_monthly_cost(
    *,
    memory_mb: int,
    monthly_invocations: float,
    average_duration_ms: float,
) -> float:
    """Estimate monthly Lambda compute + request cost from simple usage metrics."""
    gb_seconds = (
        (max(memory_mb, 0) / 1024)
        * max(monthly_invocations, 0)
        * (max(average_duration_ms, 0) / 1000)
    )
    request_cost = (max(monthly_invocations, 0) / 1_000_000) * LAMBDA_REQUEST_RATE_PER_MILLION
    return round((gb_seconds * LAMBDA_GB_SECOND_RATE) + request_cost, 2)


def estimate_s3_transition_savings(
    *,
    standard_storage_gib: float,
    eligible_fraction: float = 0.35,
    target: Literal["standard_ia", "glacier_ir"] = "standard_ia",
) -> float:
    """Estimate savings from moving a conservative fraction of standard storage to colder tiers."""
    eligible_gib = max(standard_storage_gib, 0.0) * max(min(eligible_fraction, 1.0), 0.0)
    target_rate = (
        S3_STANDARD_IA_RATE_PER_GIB_MONTH
        if target == "standard_ia"
        else S3_GLACIER_IR_RATE_PER_GIB_MONTH
    )
    return round(max(S3_STANDARD_RATE_PER_GIB_MONTH - target_rate, 0.0) * eligible_gib, 2)

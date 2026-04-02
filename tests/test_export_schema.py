import json
from pathlib import Path

from finops_pack.export_schema import (
    build_export_recommendations_schema,
    validate_export_recommendations_payload,
    write_export_recommendations_schema,
)


def test_build_export_recommendations_schema_is_array_schema() -> None:
    schema = build_export_recommendations_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "array"
    assert "items" in schema


def test_validate_export_recommendations_payload_accepts_normalized_output() -> None:
    recommendations = validate_export_recommendations_payload(
        [
            {
                "recommendation_id": "rec-1",
                "source": "cost_optimization_hub",
                "category": "rightsizing / idle deletion",
                "account_id": "123456789012",
                "region": "us-east-1",
                "resource_id": "i-1234567890abcdef0",
                "action_type": "Rightsize",
                "currency_code": "USD",
                "estimated_monthly_savings": 42.5,
                "recommendation": {
                    "code": "coh-rightsize-ec2instance",
                    "title": "Rightsize Ec2Instance",
                    "summary": "Current: m5.large. Recommended: t3.large.",
                    "action": "Rightsize the Ec2Instance.",
                    "effort": "medium",
                    "risk": "medium",
                    "savings": {
                        "monthly_low_usd": 42.5,
                        "monthly_high_usd": 42.5,
                        "annual_low_usd": 510.0,
                        "annual_high_usd": 510.0,
                    },
                },
            }
        ]
    )

    assert len(recommendations) == 1
    assert recommendations[0].recommendation_id == "rec-1"
    assert recommendations[0].estimated_monthly_savings == 42.5


def test_write_export_recommendations_schema_writes_json_file(tmp_path: Path) -> None:
    destination = tmp_path / "exports.schema.json"

    written_path = write_export_recommendations_schema(destination)

    assert written_path == destination
    schema = json.loads(destination.read_text(encoding="utf-8"))
    assert schema["title"] == "finops-pack COH Export"

"""Helpers for documenting and validating exported recommendation JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from finops_pack.domain.models import NormalizedRecommendation

EXPORT_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _export_recommendations_adapter() -> TypeAdapter[list[NormalizedRecommendation]]:
    """Return the shared adapter for normalized recommendation exports."""
    return TypeAdapter(list[NormalizedRecommendation])


def build_export_recommendations_schema() -> dict[str, Any]:
    """Build the JSON Schema for `output/exports.json`."""
    schema = _export_recommendations_adapter().json_schema()
    schema["$schema"] = EXPORT_SCHEMA_DRAFT
    schema["title"] = "finops-pack COH Export"
    schema["description"] = (
        "Normalized Cost Optimization Hub recommendations exported by finops-pack."
    )

    items = schema.get("items")
    if isinstance(items, dict):
        items.setdefault(
            "description",
            "One normalized recommendation entry from the current finops-pack run.",
        )

    return schema


def render_export_recommendations_schema() -> str:
    """Render the export JSON Schema as formatted JSON."""
    return json.dumps(build_export_recommendations_schema(), indent=2) + "\n"


def validate_export_recommendations_payload(
    payload: Any,
) -> list[NormalizedRecommendation]:
    """Validate and coerce a JSON export payload into normalized recommendations."""
    return _export_recommendations_adapter().validate_python(payload)


def write_export_recommendations_schema(destination: str | Path) -> Path:
    """Write the export JSON Schema to disk."""
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        render_export_recommendations_schema(),
        encoding="utf-8",
    )
    return destination_path

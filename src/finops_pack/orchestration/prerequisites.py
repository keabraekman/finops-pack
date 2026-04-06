"""Shared prerequisite notes and remediation guidance."""

from __future__ import annotations

CE_RESOURCE_LEVEL_DOC_NOTE = (
    "Cost Explorer resource-level daily data is opt-in and only covers the last 14 days."
)
CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE = (
    "Enable it in Billing and Cost Management preferences before you query it."
)
COH_IMPORT_NOTE = (
    "AWS notes imported recommendations are stored in us-east-1 and can take up to 24 "
    "hours to appear."
)
OPTIONAL_CE_FALLBACK_NOTE = (
    "Optional Cost Explorer fallback modules are available via "
    "--enable-ce-rightsizing-fallback and --enable-ce-savings-plan-fallback. "
    "They remain disabled by default and Cost Optimization Hub stays primary."
)

# Export JSON Schema

`finops-pack run` and `finops-pack demo` now write `output/exports.schema.json` beside
`output/exports.json`.

The schema is generated from the same typed recommendation model that powers the dashboard, so
the export structure and the in-app validation stay aligned.

## Top-level shape

- `exports.json` is a top-level array.
- Each item is one normalized Cost Optimization Hub recommendation.
- The same shape is also written to `out/normalized/recommendations.json`.

## Fields

| Field | Type | Notes |
| --- | --- | --- |
| `recommendation_id` | string | Stable identifier from AWS Cost Optimization Hub. |
| `source` | string | Currently always `cost_optimization_hub`. |
| `category` | string | One of `rightsizing / idle deletion`, `commitment (SP/RI)`, or `storage/network/etc.` |
| `account_id` | string or null | AWS account that owns the recommendation target. |
| `region` | string or null | AWS region associated with the recommendation. |
| `resource_id` | string or null | Primary AWS resource identifier. |
| `resource_arn` | string or null | Resource ARN when AWS returns one. |
| `current_resource_type` | string or null | Current AWS resource type. |
| `recommended_resource_type` | string or null | Recommended target resource type. |
| `current_resource_summary` | string or null | Human-readable current state summary from AWS when available. |
| `recommended_resource_summary` | string or null | Human-readable target state summary from AWS when available. |
| `current_resource_details` | object or null | Raw structured detail for the current resource. |
| `recommended_resource_details` | object or null | Raw structured detail for the recommendation target. |
| `action_type` | string or null | AWS action verb such as `Rightsize`, `Delete`, or `PurchaseSavingsPlans`. |
| `currency_code` | string or null | Currency for the savings estimate, usually `USD`. |
| `estimated_monthly_savings` | number or null | Monthly savings estimate normalized by AWS COH. |
| `estimated_monthly_cost` | number or null | Monthly cost estimate when AWS returns one. |
| `estimated_savings_percentage` | number or null | Percentage savings estimate when AWS returns one. |
| `recommendation` | object or null | Finops-pack’s report-friendly normalized recommendation object. |

## Nested `recommendation` fields

| Field | Type | Notes |
| --- | --- | --- |
| `code` | string | Stable finops-pack recommendation code. |
| `title` | string | Short human-readable title used in tables and exports. |
| `summary` | string | Human-readable current and recommended state summary. |
| `action` | string | Operator-facing action statement. |
| `effort` | string | One of `low`, `medium`, or `high`. |
| `risk` | string | One of `low`, `medium`, or `high`. |
| `savings` | object or null | Optional normalized savings range. |

## Nested `recommendation.savings` fields

| Field | Type | Notes |
| --- | --- | --- |
| `monthly_low_usd` | number | Lower bound monthly estimate in USD. |
| `monthly_high_usd` | number | Upper bound monthly estimate in USD. |
| `annual_low_usd` | number or null | Lower bound annual estimate in USD. |
| `annual_high_usd` | number or null | Upper bound annual estimate in USD. |

## Validation

- The schema file is generated from `finops_pack.reporting.export_schema`.
- Tests validate exported payloads against the same typed model before they are written or reused in demo mode.

# Architecture

## Purpose

`finops-pack` is a lightweight AWS FinOps analysis tool that:

- collects usage and configuration data from one or more AWS accounts
- analyzes that data for cost-saving opportunities
- renders findings into human-readable reports
- exports or publishes results in simple formats

The design goal is to keep the system **modular, read-heavy, and low-risk**.

---

## Core components

### 1. CLI
Entry point: `finops_pack/cli.py`

Responsibilities:

- parse command-line arguments
- load config
- start the scan flow
- select output format(s)
- control demo vs real AWS mode

---

### 2. Config loader
Responsibilities:

- load runtime settings from CLI flags, env vars, or config files
- validate required inputs
- centralize account, region, and output settings

This keeps business logic separate from runtime configuration.

---

### 3. AWS auth / assume role
Entry point: `finops_pack/aws/assume_role.py`

Responsibilities:

- assume a read-only cross-account role in target AWS accounts
- return session credentials for downstream collectors
- isolate credential handling from the rest of the app

This is the trust boundary between the tool and AWS.

---

### 4. Collectors
Folder: `finops_pack/collectors/`

Responsibilities:

- call AWS APIs
- gather raw resource and billing-related metadata
- normalize responses into internal models

Examples later may include collectors for:

- EC2
- EBS
- RDS
- Lambda
- S3
- CloudWatch / idle metrics
- CUR-derived inputs if enabled

Collectors should **only collect**, not decide savings recommendations.

---

### 5. Analyzers
Folder: `finops_pack/analyzers/`

Responsibilities:

- inspect normalized resource data
- apply heuristics and rules
- generate findings and savings estimates

Examples:

- unattached EBS volume detection
- underutilized EC2 recommendations
- old snapshots
- idle load balancers
- stopped-but-costing resources

Analyzers produce structured `Finding` objects, not presentation output.

---

### 6. Data models
Responsibilities:

- define shared schemas such as:
  - `Resource`
  - `SavingsRange`
  - `Recommendation`
  - `Finding`

These models provide a stable contract between collectors, analyzers, and renderers.

---

### 7. Render layer
Folder: `finops_pack/render/`

Responsibilities:

- convert findings into user-facing output
- support HTML templating via Jinja2
- keep formatting logic separate from analysis logic

Typical outputs:

- summary tables
- grouped findings
- savings rollups
- account / region sections

---

### 8. Exporters / publishers
Folders:
- `finops_pack/render/`
- `finops_pack/publish/`

Responsibilities:

- export findings to JSON / CSV / HTML
- optionally upload reports to S3
- optionally generate presigned URLs for report access

This layer is the only place that should perform optional write actions.

---

### 9. Demo fixtures
Folder: `demo/fixtures/`

Responsibilities:

- provide sample input files for local development
- support testing without AWS access
- make UI/report work reproducible before real collectors are complete

---

## Data flow

High-level flow:

1. User runs CLI command.
2. Config is loaded and validated.
3. Tool determines target account(s), region(s), and mode.
4. AWS role is assumed for each target account.
5. Collectors gather raw resource data.
6. Raw data is normalized into internal models.
7. Analyzers evaluate the data and produce findings.
8. Renderers build report content.
9. Exporters write JSON / CSV / HTML outputs.
10. Publishers optionally upload reports and return a shareable link.

---

## Design principles

### Separation of concerns
Each layer has one job:

- collectors gather
- analyzers reason
- renderers format
- publishers distribute

### Small, composable modules
New AWS checks should be easy to add without changing the whole system.

### Deterministic output
Given the same input data, analyzers should produce the same findings.

### Safe by default
The default system should avoid changing customer infrastructure.

---

## Permissions philosophy

This project should follow a strict **least-privilege** model.

### Read-only by default
Core analysis should require only read/list/describe permissions wherever possible.

Examples:

- `Describe*`
- `List*`
- metric reads
- CUR/S3 reads if billing data is used

### No infrastructure mutation
The tool should **not** create, modify, stop, terminate, or delete customer resources as part of normal operation.

That means no default permissions for actions like:

- `TerminateInstances`
- `DeleteVolume`
- `StopDBInstance`
- `PutBucketPolicy` on customer-owned data buckets

### Isolated write permissions
If publishing is enabled, write access should be limited to a dedicated output location, such as:

- one controlled S3 bucket for reports
- presigned URL generation for already-uploaded artifacts

### Cross-account access via assume-role
For multi-account setups, target accounts should expose a narrowly scoped read-only role that this tool assumes temporarily.

### Clear trust boundary
Credentials should be short-lived, scoped, and never hardcoded.

---

## Future extension points

Likely future additions:

- more collectors and analyzers
- richer CUR-based savings estimates
- suppression / ignore rules
- severity levels
- account tagging and grouping
- scheduled execution
- dashboard or API layer

The current structure is meant to support those additions without major refactoring.
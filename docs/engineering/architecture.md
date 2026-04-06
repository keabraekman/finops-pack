# Architecture

`finops-pack` is one repository organized around product workflows.

## Boundaries

- `api/`: customer-facing FastAPI website, templates, static files, validation UX, and status pages
- `worker/`: background executable and job handlers
- `jobs/`: queue abstraction, job message model, retry policy, and state machine
- `domain/`: lead, account scope, assessment run, discovered account, artifact, and job state concepts
- `use_cases/`: workflow boundaries for intake, validation, enqueueing, discovery, and run status
- `integrations/`: AWS, DB/storage, notifications, and policy helpers
- `analysis/`: opportunity analysis and savings logic
- `reporting/`: generated dashboard, appendix, exporters, static assets, and report templates
- `orchestration/`: CLI config, demo fixtures, and prerequisite glue

## Runtime

The website accepts intake and queues assessment work. The worker performs the expensive scan/report workflow out-of-band. Both processes share the same repository, package, environment configuration, and persistence path.

For v1, SQLite is intentionally used for speed and clarity. The queue and storage boundaries are explicit so production hardening can swap the backing services later without rewriting the product flow.

# finops-pack

AWS Savings Review is a customer-facing lead-magnet website plus a background worker and the existing FinOps report generator.

The website guides a prospect through read-only AWS setup, captures intake for either one AWS account or an AWS Organization, queues a background assessment job, and serves the generated dashboard and appendix in-browser.

![Dashboard preview](docs/assets/dashboard-demo.svg)

## Product Flow

1. A prospect opens the AWS Savings Review site.
2. They follow the setup guide for Cost Explorer, Cost Optimization Hub, and one read-only cross-account IAM role.
3. They choose the assessment scope: one AWS account or an AWS Organization.
4. They submit company/contact details, work email, role ARN, external ID, and prerequisite confirmations.
5. The API validates assume-role access and queues an assessment job.
6. The worker picks up the job, attempts Organization discovery when requested, runs the existing report generator, stores artifacts, and sends notifications.
7. The prospect views the dashboard, appendix, and prior report history.

## Repository Layout

```text
src/finops_pack/api/            customer-facing FastAPI website
src/finops_pack/worker/         background worker executable and handlers
src/finops_pack/jobs/           local queue, job messages, retry policy, state transitions
src/finops_pack/domain/         lead, scope, run, artifact, and job domain models
src/finops_pack/use_cases/      product workflow boundaries
src/finops_pack/integrations/   AWS, storage, notifications, DB, and policy integrations
src/finops_pack/analysis/       savings/opportunity analysis logic
src/finops_pack/reporting/      dashboard/appendix rendering and exports
src/finops_pack/orchestration/  CLI config, demo fixtures, and prerequisite glue
infra/aws/                      CloudFormation, IAM policies, container Dockerfiles, examples
docs/product/                   product and intake docs
docs/engineering/               architecture, job model, and report schema docs
```

## Local Development

Install dependencies:

```bash
uv sync --dev
```

Run the website:

```bash
cp .env.example .env.local
set -a
source .env.local
set +a
uv run finops-pack-web
```

Run the worker in a second terminal:

```bash
set -a
source .env.local
set +a
uv run finops-pack-worker
```

For local one-job testing:

```bash
uv run finops-pack-worker --once
```

Open `http://localhost:8000/`.

## CLI

The CLI remains available for local development and operator workflows:

```bash
uv run finops-pack run \
  --role-arn arn:aws:iam::123456789012:role/aws-savings-review-readonly \
  --external-id replace-me \
  --client my-startup \
  --regions us-east-1 us-west-1 \
  --collect-ce-resource-daily \
  --check-identity \
  --no-upload
```

Demo mode renders from scrubbed fixtures without AWS credentials:

```bash
uv run finops-pack demo
```

## AWS Setup Assets

- CloudFormation role template: `infra/aws/cloudformation/readonly-role.yaml`
- Minimal read-only policy: `infra/aws/iam/policy-min.json`
- Optional fuller policy: `infra/aws/iam/policy-full.json`
- Example config: `infra/aws/examples/config.example.yaml`

The website setup page also renders copy-paste trust and permissions policies using the configured trusted AWS account ID and generated external ID.

## Containers

Build the website container:

```bash
docker build -f infra/aws/containers/web.Dockerfile -t finops-pack-web .
```

Build the worker container:

```bash
docker build -f infra/aws/containers/worker.Dockerfile -t finops-pack-worker .
```

For v1, deploy the web and worker containers against the same persistent `FINOPS_WEB_DATA_DIR` or database path.

## Validation

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```

`uv run mypy .` is still useful, but this repo currently has older typing debt outside the web/worker refactor path.

## Docs

- Product intake flow: `docs/product/intake-flow.md`
- Account scope behavior: `docs/product/account-scope.md`
- Permissions explanation: `docs/product/permissions-explained.md`
- Architecture: `docs/engineering/architecture.md`
- Job model: `docs/engineering/job-model.md`
- Report schema: `docs/engineering/report-schema.md`

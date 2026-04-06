# AWS Savings Review Website

The website is the primary product surface for v1. It is a FastAPI app under `src/finops_pack/api/` with server-rendered templates and a simple, professional intake flow.

## What It Does

- Explains read-only AWS prerequisites
- Generates copy-paste IAM trust and permissions policy snippets
- Captures one-account or AWS Organization intake
- Validates cross-account assume-role access
- Creates a lead and assessment run
- Queues background assessment work
- Shows status, history, dashboard, and appendix pages

## Runtime Components

- API entrypoint: `finops-pack-web`
- Worker entrypoint: `finops-pack-worker`
- SQLite lead/run storage: `src/finops_pack/api/storage.py`
- SQLite job queue: `src/finops_pack/jobs/queue.py`
- Report runner: `src/finops_pack/api/runner.py`

For v1, SQLite keeps deployment simple. In production, the web and worker containers should share the same persistent database and report artifact storage.

## Manual Configuration

Set these before production use:

- `FINOPS_WEB_TRUSTED_AWS_ACCOUNT_ID`: AWS account ID shown in the trust policy
- `FINOPS_WEB_BASE_URL`: public HTTPS base URL
- `FINOPS_WEB_REPORT_CTA_URL`: scheduling or implementation-review link
- SMTP variables in `.env.example`: sender, host, credentials, and notification email

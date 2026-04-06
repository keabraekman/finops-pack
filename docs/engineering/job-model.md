# Job Model

The web app and worker communicate through a small SQLite-backed queue in `src/finops_pack/jobs/`.

## States

- `pending`: accepted by the API and ready for a worker
- `running`: claimed by a worker
- `completed`: worker finished successfully
- `retryable_failure`: worker failed but another attempt is allowed
- `failed`: worker exhausted retries or failed terminally

Customer-visible run states remain stored on the assessment run:

- `QUEUED`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`

## Local Queue

`SQLiteJobQueue` stores job messages in the same SQLite database used for leads and runs. This keeps v1 local and container deployment simple while leaving a clean seam for SQS, Postgres, or another production queue later.

## Worker

`finops-pack-worker` claims one job at a time, runs Organization discovery for org-mode jobs, calls the existing report runner, updates job state, and leaves generated artifacts registered on the run.


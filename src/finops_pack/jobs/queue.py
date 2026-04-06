"""SQLite-backed local queue for assessment jobs."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from finops_pack.domain.models.assessment import AccountScopeType, JobStatus
from finops_pack.jobs.messages import AssessmentJobMessage
from finops_pack.jobs.retry_policy import RetryPolicy


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _token(prefix: str) -> str:
    suffix = secrets.token_urlsafe(9).replace("_", "").replace("-", "")
    return f"{prefix}_{suffix[:14]}"


class SQLiteJobQueue:
    """Simple durable queue backed by the app SQLite database."""

    def __init__(self, database_path: Path, retry_policy: RetryPolicy | None = None) -> None:
        self._database_path = database_path
        self._retry_policy = retry_policy or RetryPolicy()

    def initialize(self) -> None:
        """Create the job table if it does not exist."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS assessment_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    run_public_id TEXT NOT NULL UNIQUE,
                    account_scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    not_before TEXT,
                    error_summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_assessment_jobs_status
                ON assessment_jobs(status, not_before, id);
                """
            )

    def enqueue_assessment(
        self,
        *,
        run_public_id: str,
        account_scope: AccountScopeType,
    ) -> AssessmentJobMessage:
        """Queue an assessment run, returning the existing job if one already exists."""
        now = _utcnow()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM assessment_jobs WHERE run_public_id = ?",
                (run_public_id,),
            ).fetchone()
            if row is None:
                public_id = _token("job")
                connection.execute(
                    """
                    INSERT INTO assessment_jobs (
                        public_id,
                        run_public_id,
                        account_scope,
                        status,
                        attempts,
                        max_attempts,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        public_id,
                        run_public_id,
                        account_scope.value,
                        JobStatus.PENDING.value,
                        0,
                        self._retry_policy.max_attempts,
                        now,
                        now,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM assessment_jobs WHERE run_public_id = ?",
                (run_public_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Failed to enqueue assessment job.")
        return self._message_from_row(row)

    def claim_next(self) -> AssessmentJobMessage | None:
        """Claim the next pending or retryable job for worker execution."""
        now = _utcnow()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM assessment_jobs
                WHERE status IN (?, ?)
                  AND attempts < max_attempts
                  AND (not_before IS NULL OR not_before <= ?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (JobStatus.PENDING.value, JobStatus.RETRYABLE_FAILURE.value, now),
            ).fetchone()
            if row is None:
                return None
            attempt = int(row["attempts"]) + 1
            connection.execute(
                """
                UPDATE assessment_jobs
                SET status = ?,
                    attempts = ?,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?,
                    error_summary = NULL
                WHERE id = ?
                """,
                (
                    JobStatus.RUNNING.value,
                    attempt,
                    now,
                    now,
                    int(row["id"]),
                ),
            )
            claimed = connection.execute(
                "SELECT * FROM assessment_jobs WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
        return self._message_from_row(claimed) if claimed is not None else None

    def mark_completed(self, job_public_id: str) -> None:
        """Mark a job completed."""
        self._update_status(
            job_public_id,
            status=JobStatus.COMPLETED,
            error_summary=None,
            not_before=None,
            finished=True,
        )

    def mark_failure(self, job_public_id: str, *, error_summary: str, attempt: int) -> JobStatus:
        """Mark a job as retryable or failed according to the retry policy."""
        if self._retry_policy.should_retry(attempt):
            status = JobStatus.RETRYABLE_FAILURE
            not_before = self._retry_policy.next_not_before(attempt)
            finished = False
        else:
            status = JobStatus.FAILED
            not_before = None
            finished = True
        self._update_status(
            job_public_id,
            status=status,
            error_summary=error_summary,
            not_before=not_before,
            finished=finished,
        )
        return status

    def _update_status(
        self,
        job_public_id: str,
        *,
        status: JobStatus,
        error_summary: str | None,
        not_before: str | None,
        finished: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE assessment_jobs
                SET status = ?,
                    error_summary = ?,
                    not_before = ?,
                    updated_at = ?,
                    finished_at = CASE WHEN ? THEN ? ELSE finished_at END
                WHERE public_id = ?
                """,
                (
                    status.value,
                    error_summary,
                    not_before,
                    _utcnow(),
                    1 if finished else 0,
                    _utcnow(),
                    job_public_id,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _message_from_row(self, row: sqlite3.Row) -> AssessmentJobMessage:
        return AssessmentJobMessage(
            job_public_id=str(row["public_id"]),
            run_public_id=str(row["run_public_id"]),
            account_scope=AccountScopeType.from_form_value(str(row["account_scope"])),
            attempt=int(row["attempts"]),
        )


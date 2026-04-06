"""Allowed transitions for background jobs."""

from __future__ import annotations

from finops_pack.domain.models.assessment import JobStatus

ALLOWED_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.RUNNING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.RETRYABLE_FAILURE,
    },
    JobStatus.RETRYABLE_FAILURE: {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    """Return True when a job can move from current to target."""
    return target in ALLOWED_JOB_TRANSITIONS[current]


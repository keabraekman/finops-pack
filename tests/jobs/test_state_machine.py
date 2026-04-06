from finops_pack.domain.models.assessment import JobStatus
from finops_pack.jobs.state_machine import can_transition


def test_job_state_machine_allows_expected_transitions() -> None:
    assert can_transition(JobStatus.PENDING, JobStatus.RUNNING)
    assert can_transition(JobStatus.RUNNING, JobStatus.COMPLETED)
    assert can_transition(JobStatus.RUNNING, JobStatus.RETRYABLE_FAILURE)
    assert can_transition(JobStatus.RETRYABLE_FAILURE, JobStatus.RUNNING)
    assert not can_transition(JobStatus.COMPLETED, JobStatus.RUNNING)


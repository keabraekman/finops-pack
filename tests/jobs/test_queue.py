from pathlib import Path

from finops_pack.domain.models.assessment import AccountScopeType
from finops_pack.jobs.queue import SQLiteJobQueue
from finops_pack.jobs.retry_policy import RetryPolicy


def test_sqlite_job_queue_enqueues_claims_and_completes_assessment(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(
        tmp_path / "leadgen.sqlite3",
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    queue.initialize()

    enqueued = queue.enqueue_assessment(
        run_public_id="run_123",
        account_scope=AccountScopeType.ORGANIZATION,
    )
    assert enqueued.run_public_id == "run_123"
    assert enqueued.account_scope == AccountScopeType.ORGANIZATION

    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.attempt == 1
    assert claimed.account_scope == AccountScopeType.ORGANIZATION

    queue.mark_completed(claimed.job_public_id)
    assert queue.claim_next() is None


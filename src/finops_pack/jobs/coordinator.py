"""Job coordination API used by the website."""

from __future__ import annotations

from finops_pack.domain.models.assessment import AccountScopeType
from finops_pack.jobs.messages import AssessmentJobMessage
from finops_pack.jobs.queue import SQLiteJobQueue


class JobCoordinator:
    """Small facade for enqueueing assessment work."""

    def __init__(self, queue: SQLiteJobQueue) -> None:
        self._queue = queue

    def enqueue_assessment(
        self,
        *,
        run_public_id: str,
        account_scope: AccountScopeType,
    ) -> AssessmentJobMessage:
        """Create or return a queued assessment job."""
        return self._queue.enqueue_assessment(
            run_public_id=run_public_id,
            account_scope=account_scope,
        )

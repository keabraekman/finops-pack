"""Worker poller for local assessment jobs."""

from __future__ import annotations

from finops_pack.api.storage import SQLiteLeadStore
from finops_pack.jobs.queue import SQLiteJobQueue
from finops_pack.worker.handlers.assessment import AssessmentJobHandler


class WorkerPoller:
    """Poll the queue once or in a service loop."""

    def __init__(
        self,
        *,
        queue: SQLiteJobQueue,
        handler: AssessmentJobHandler,
        store: SQLiteLeadStore,
    ) -> None:
        self._queue = queue
        self._handler = handler
        self._store = store

    def run_once(self) -> bool:
        """Process one queued job. Return True when work was claimed."""
        message = self._queue.claim_next()
        if message is None:
            return False

        try:
            self._handler.handle(message)
        except Exception as exc:
            status = self._queue.mark_failure(
                message.job_public_id,
                error_summary=str(exc),
                attempt=message.attempt,
            )
            if status.value == "failed":
                self._store.mark_run_failed_unstarted(
                    run_public_id=message.run_public_id,
                    error_summary=str(exc),
                    process_log="Worker failed before the report runner completed.",
                )
            return True

        self._queue.mark_completed(message.job_public_id)
        return True


"""Worker executable for queued AWS Savings Review assessments."""

from __future__ import annotations

import argparse
import time

from finops_pack.api.emailer import EmailService
from finops_pack.api.runner import RunOrchestrator
from finops_pack.api.settings import load_web_settings
from finops_pack.api.storage import SQLiteLeadStore
from finops_pack.jobs.queue import SQLiteJobQueue
from finops_pack.worker.handlers.assessment import AssessmentJobHandler
from finops_pack.worker.poller import WorkerPoller


def build_poller() -> WorkerPoller:
    """Build a worker poller from environment settings."""
    settings = load_web_settings()
    store = SQLiteLeadStore(settings.database_path)
    store.initialize()
    queue = SQLiteJobQueue(settings.database_path)
    queue.initialize()
    email_service = EmailService(settings)
    orchestrator = RunOrchestrator(settings, store, email_service)
    handler = AssessmentJobHandler(store=store, orchestrator=orchestrator)
    return WorkerPoller(queue=queue, handler=handler, store=store)


def main() -> None:
    """Run the background worker."""
    parser = argparse.ArgumentParser(description="Run AWS Savings Review background jobs.")
    parser.add_argument("--once", action="store_true", help="Process one job and exit.")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args()

    poller = build_poller()
    if args.once:
        poller.run_once()
        return

    while True:
        claimed_work = poller.run_once()
        if not claimed_work:
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()

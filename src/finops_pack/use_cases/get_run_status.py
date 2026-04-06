"""Run status use case."""

from __future__ import annotations

from finops_pack.api.storage import RunRecord, SQLiteLeadStore


def get_run_status(*, store: SQLiteLeadStore, run_public_id: str) -> RunRecord | None:
    """Return the stored run status for status pages and API checks."""
    return store.get_run_by_public_id(run_public_id)


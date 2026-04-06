"""Worker progress messages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerProgress:
    """Human-readable worker progress event."""

    run_public_id: str
    message: str


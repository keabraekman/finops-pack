"""SQLite persistence for leads, runs, and generated report artifacts."""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _token(prefix: str) -> str:
    suffix = secrets.token_urlsafe(9).replace("_", "").replace("-", "")
    return f"{prefix}_{suffix[:14]}"


@dataclass(frozen=True)
class LeadRecord:
    """Stored lead information."""

    id: int
    public_id: str
    email: str
    company_name: str | None
    contact_name: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ArtifactRecord:
    """A generated artifact tied to a run."""

    id: int
    run_id: int
    kind: str
    relative_path: str
    created_at: str


@dataclass(frozen=True)
class RunRecord:
    """Stored run metadata and report paths."""

    id: int
    public_id: str
    lead_id: int | None
    lead_public_id: str | None
    lead_email: str | None
    account_scope: str
    role_arn: str
    external_id: str
    generated_external_id: str | None
    company_name: str | None
    contact_name: str | None
    notes: str | None
    validation_status: str
    validation_payload: dict[str, Any]
    status: str
    error_summary: str | None
    process_log: str | None
    account_id: str | None
    created_at: str
    validated_at: str
    started_at: str | None
    finished_at: str | None
    workspace_dir: str | None
    report_dir: str | None
    dashboard_path: str | None
    appendix_path: str | None
    preview_path: str | None
    summary_path: str | None
    artifacts: tuple[ArtifactRecord, ...]


class SQLiteLeadStore:
    """Tiny SQLite data layer for the v1 lead-gen site."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        """Create the database schema when missing."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    company_name TEXT,
                    contact_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
                    account_scope TEXT NOT NULL DEFAULT 'single_account',
                    role_arn TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    generated_external_id TEXT,
                    company_name TEXT,
                    contact_name TEXT,
                    notes TEXT,
                    validation_status TEXT NOT NULL,
                    validation_payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_summary TEXT,
                    process_log TEXT,
                    account_id TEXT,
                    created_at TEXT NOT NULL,
                    validated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    workspace_dir TEXT,
                    report_dir TEXT,
                    dashboard_path TEXT,
                    appendix_path TEXT,
                    preview_path TEXT,
                    summary_path TEXT
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(
                connection,
                table_name="runs",
                column_name="account_scope",
                column_definition="TEXT NOT NULL DEFAULT 'single_account'",
            )

    def create_validated_run_draft(
        self,
        *,
        role_arn: str,
        external_id: str,
        generated_external_id: str | None,
        company_name: str | None,
        contact_name: str | None,
        notes: str | None,
        validation_payload: dict[str, Any],
        account_scope: str = "single_account",
        status: str = "AWAITING_EMAIL",
    ) -> RunRecord:
        """Persist a run after AWS validation but before the email step."""
        now = _utcnow()
        public_id = _token("run")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    public_id,
                    account_scope,
                    role_arn,
                    external_id,
                    generated_external_id,
                    company_name,
                    contact_name,
                    notes,
                    validation_status,
                    validation_payload,
                    status,
                    created_at,
                    validated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id,
                    account_scope,
                    role_arn,
                    external_id,
                    generated_external_id,
                    company_name,
                    contact_name,
                    notes,
                    "READY",
                    json.dumps(validation_payload, sort_keys=True),
                    status,
                    now,
                    now,
                ),
            )
        run = self.get_run_by_public_id(public_id)
        if run is None:
            raise RuntimeError("Failed to load the newly created run draft.")
        return run

    def create_or_update_lead(
        self,
        *,
        email: str,
        company_name: str | None,
        contact_name: str | None,
    ) -> LeadRecord:
        """Create or update a lead record keyed by email address."""
        normalized_email = email.strip().lower()
        now = _utcnow()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM leads WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO leads (
                        public_id,
                        email,
                        company_name,
                        contact_name,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _token("lead"),
                        normalized_email,
                        company_name,
                        contact_name,
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE leads
                    SET company_name = COALESCE(?, company_name),
                        contact_name = COALESCE(?, contact_name),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        company_name,
                        contact_name,
                        now,
                        int(row["id"]),
                    ),
                )
        lead = self.get_lead_by_email(normalized_email)
        if lead is None:
            raise RuntimeError("Failed to load the lead record after upsert.")
        return lead

    def get_lead_by_email(self, email: str) -> LeadRecord | None:
        """Return a lead by email address."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM leads WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
        return self._lead_from_row(row) if row is not None else None

    def get_lead_by_public_id(self, public_id: str) -> LeadRecord | None:
        """Return a lead by its public token."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM leads WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        return self._lead_from_row(row) if row is not None else None

    def attach_lead_to_run(self, *, run_public_id: str, lead_id: int) -> None:
        """Attach a validated run draft to a lead record."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET lead_id = ? WHERE public_id = ?",
                (lead_id, run_public_id),
            )

    def mark_run_queued(self, run_public_id: str) -> None:
        """Mark a run ready for background execution."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ? WHERE public_id = ?",
                ("QUEUED", run_public_id),
            )

    def merge_run_validation_payload(
        self,
        *,
        run_public_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Merge additional workflow details into the stored validation payload."""
        run = self.get_run_by_public_id(run_public_id)
        if run is None:
            raise RuntimeError("Run not found while updating validation payload.")
        next_payload = {**run.validation_payload, **updates}
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET validation_payload = ? WHERE public_id = ?",
                (json.dumps(next_payload, sort_keys=True), run_public_id),
            )

    def mark_run_running(
        self,
        *,
        run_public_id: str,
        workspace_dir: Path,
        report_dir: Path,
    ) -> None:
        """Mark a run as actively executing."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    started_at = ?,
                    workspace_dir = ?,
                    report_dir = ?
                WHERE public_id = ?
                """,
                (
                    "RUNNING",
                    _utcnow(),
                    str(workspace_dir),
                    str(report_dir),
                    run_public_id,
                ),
            )

    def mark_run_failed(
        self,
        *,
        run_public_id: str,
        error_summary: str,
        process_log: str,
        workspace_dir: Path,
        report_dir: Path,
    ) -> None:
        """Persist a failed run and its friendly error summary."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    error_summary = ?,
                    process_log = ?,
                    finished_at = ?,
                    workspace_dir = ?,
                    report_dir = ?
                WHERE public_id = ?
                """,
                (
                    "FAILED",
                    error_summary,
                    process_log,
                    _utcnow(),
                    str(workspace_dir),
                    str(report_dir),
                    run_public_id,
                ),
            )

    def mark_run_failed_unstarted(
        self,
        *,
        run_public_id: str,
        error_summary: str,
        process_log: str = "",
    ) -> None:
        """Persist a failed run before the report workspace exists."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    error_summary = ?,
                    process_log = ?,
                    finished_at = ?
                WHERE public_id = ?
                """,
                (
                    "FAILED",
                    error_summary,
                    process_log,
                    _utcnow(),
                    run_public_id,
                ),
            )

    def mark_run_succeeded(
        self,
        *,
        run_public_id: str,
        account_id: str | None,
        process_log: str,
        workspace_dir: Path,
        report_dir: Path,
        artifact_paths: dict[str, str],
    ) -> None:
        """Persist a successful run and register its output artifacts."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM runs WHERE public_id = ?",
                (run_public_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Run not found while marking success.")
            run_id = int(row["id"])
            connection.execute(
                """
                UPDATE runs
                SET status = ?,
                    account_id = ?,
                    process_log = ?,
                    finished_at = ?,
                    workspace_dir = ?,
                    report_dir = ?,
                    dashboard_path = ?,
                    appendix_path = ?,
                    preview_path = ?,
                    summary_path = ?,
                    error_summary = NULL
                WHERE public_id = ?
                """,
                (
                    "SUCCEEDED",
                    account_id,
                    process_log,
                    _utcnow(),
                    str(workspace_dir),
                    str(report_dir),
                    artifact_paths.get("dashboard"),
                    artifact_paths.get("appendix"),
                    artifact_paths.get("preview"),
                    artifact_paths.get("summary"),
                    run_public_id,
                ),
            )
            connection.execute("DELETE FROM artifacts WHERE run_id = ?", (run_id,))
            for kind, relative_path in artifact_paths.items():
                connection.execute(
                    """
                    INSERT INTO artifacts (run_id, kind, relative_path, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        kind,
                        relative_path,
                        _utcnow(),
                    ),
                )

    def get_run_by_public_id(self, public_id: str) -> RunRecord | None:
        """Return a run by its public token, including artifacts."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    runs.*,
                    leads.public_id AS lead_public_id,
                    leads.email AS lead_email
                FROM runs
                LEFT JOIN leads ON leads.id = runs.lead_id
                WHERE runs.public_id = ?
                """,
                (public_id,),
            ).fetchone()
            if row is None:
                return None
            artifacts = self._artifacts_for_run(connection, int(row["id"]))
        return self._run_from_row(row, artifacts)

    def list_runs_for_lead_public_id(self, lead_public_id: str) -> tuple[RunRecord, ...]:
        """List prior runs for a lead, newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    runs.*,
                    leads.public_id AS lead_public_id,
                    leads.email AS lead_email
                FROM runs
                INNER JOIN leads ON leads.id = runs.lead_id
                WHERE leads.public_id = ?
                ORDER BY runs.created_at DESC
                """,
                (lead_public_id,),
            ).fetchall()
            runs: list[RunRecord] = []
            for row in rows:
                runs.append(
                    self._run_from_row(
                        row,
                        self._artifacts_for_run(connection, int(row["id"])),
                    )
                )
        return tuple(runs)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        *,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )

    def _artifacts_for_run(
        self,
        connection: sqlite3.Connection,
        run_id: int,
    ) -> tuple[ArtifactRecord, ...]:
        rows = connection.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY kind ASC",
            (run_id,),
        ).fetchall()
        return tuple(
            ArtifactRecord(
                id=int(row["id"]),
                run_id=int(row["run_id"]),
                kind=str(row["kind"]),
                relative_path=str(row["relative_path"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        )

    def _lead_from_row(self, row: sqlite3.Row) -> LeadRecord:
        return LeadRecord(
            id=int(row["id"]),
            public_id=str(row["public_id"]),
            email=str(row["email"]),
            company_name=(
                str(row["company_name"]) if row["company_name"] is not None else None
            ),
            contact_name=(
                str(row["contact_name"]) if row["contact_name"] is not None else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _run_from_row(
        self,
        row: sqlite3.Row,
        artifacts: tuple[ArtifactRecord, ...],
    ) -> RunRecord:
        payload_raw = row["validation_payload"]
        validation_payload = (
            json.loads(str(payload_raw))
            if isinstance(payload_raw, str) and payload_raw
            else {}
        )
        return RunRecord(
            id=int(row["id"]),
            public_id=str(row["public_id"]),
            lead_id=int(row["lead_id"]) if row["lead_id"] is not None else None,
            lead_public_id=(
                str(row["lead_public_id"]) if row["lead_public_id"] is not None else None
            ),
            lead_email=str(row["lead_email"]) if row["lead_email"] is not None else None,
            account_scope=(
                str(row["account_scope"]) if row["account_scope"] is not None else "single_account"
            ),
            role_arn=str(row["role_arn"]),
            external_id=str(row["external_id"]),
            generated_external_id=(
                str(row["generated_external_id"])
                if row["generated_external_id"] is not None
                else None
            ),
            company_name=(
                str(row["company_name"]) if row["company_name"] is not None else None
            ),
            contact_name=(
                str(row["contact_name"]) if row["contact_name"] is not None else None
            ),
            notes=str(row["notes"]) if row["notes"] is not None else None,
            validation_status=str(row["validation_status"]),
            validation_payload=validation_payload,
            status=str(row["status"]),
            error_summary=(
                str(row["error_summary"]) if row["error_summary"] is not None else None
            ),
            process_log=str(row["process_log"]) if row["process_log"] is not None else None,
            account_id=str(row["account_id"]) if row["account_id"] is not None else None,
            created_at=str(row["created_at"]),
            validated_at=str(row["validated_at"]),
            started_at=str(row["started_at"]) if row["started_at"] is not None else None,
            finished_at=(
                str(row["finished_at"]) if row["finished_at"] is not None else None
            ),
            workspace_dir=(
                str(row["workspace_dir"]) if row["workspace_dir"] is not None else None
            ),
            report_dir=str(row["report_dir"]) if row["report_dir"] is not None else None,
            dashboard_path=(
                str(row["dashboard_path"]) if row["dashboard_path"] is not None else None
            ),
            appendix_path=(
                str(row["appendix_path"]) if row["appendix_path"] is not None else None
            ),
            preview_path=(
                str(row["preview_path"]) if row["preview_path"] is not None else None
            ),
            summary_path=str(row["summary_path"]) if row["summary_path"] is not None else None,
            artifacts=artifacts,
        )

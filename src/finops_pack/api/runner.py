"""Background execution wrapper around the existing report CLI."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from finops_pack.api.emailer import EmailService
from finops_pack.api.settings import WebSettings
from finops_pack.api.storage import RunRecord, SQLiteLeadStore


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "client"


class RunOrchestrator:
    """Run the report generator in the background and persist artifact paths."""

    def __init__(
        self,
        settings: WebSettings,
        store: SQLiteLeadStore,
        email_service: EmailService,
    ) -> None:
        self._settings = settings
        self._store = store
        self._email_service = email_service

    def run_report(self, run_public_id: str) -> None:
        """Execute a stored run through the existing report CLI."""
        run = self._store.get_run_by_public_id(run_public_id)
        if run is None:
            return

        workspace_dir = self._settings.runs_dir / run_public_id
        report_dir = workspace_dir / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        self._store.mark_run_running(
            run_public_id=run_public_id,
            workspace_dir=workspace_dir,
            report_dir=report_dir,
        )

        command = self._build_command(run_public_id=run_public_id, report_dir=report_dir, run=run)
        completed = subprocess.run(
            command,
            cwd=self._settings.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        process_log = self._build_process_log(command, completed.stdout, completed.stderr)

        if completed.returncode != 0:
            self._store.mark_run_failed(
                run_public_id=run_public_id,
                error_summary=self._friendly_failure_summary(completed.stdout, completed.stderr),
                process_log=process_log,
                workspace_dir=workspace_dir,
                report_dir=report_dir,
            )
            failed_run = self._store.get_run_by_public_id(run_public_id)
            lead = (
                self._store.get_lead_by_public_id(failed_run.lead_public_id)
                if failed_run and failed_run.lead_public_id
                else None
            )
            if failed_run is not None:
                self._email_service.send_internal_run_failed(lead, failed_run)
            return

        artifact_paths = self._collect_artifact_paths(report_dir)
        account_id = self._read_account_id(report_dir)
        self._store.mark_run_succeeded(
            run_public_id=run_public_id,
            account_id=account_id,
            process_log=process_log,
            workspace_dir=workspace_dir,
            report_dir=report_dir,
            artifact_paths=artifact_paths,
        )
        finished_run = self._store.get_run_by_public_id(run_public_id)
        if finished_run is None:
            return
        lead = (
            self._store.get_lead_by_public_id(finished_run.lead_public_id)
            if finished_run.lead_public_id
            else None
        )
        if lead is not None:
            self._email_service.send_lead_report_ready(lead, finished_run)
        self._email_service.send_internal_report_ready(lead, finished_run)

    def _build_command(self, *, run_public_id: str, report_dir: Path, run: RunRecord) -> list[str]:
        company_name = run.company_name
        contact_name = run.contact_name
        client_basis = company_name or contact_name or run_public_id
        client_id = f"{_slugify(str(client_basis))}-{run_public_id[-6:]}"
        resolved_regions = self._resolved_regions_for_run(run)

        command = [
            sys.executable,
            "-m",
            "finops_pack",
            "run",
            "--role-arn",
            run.role_arn,
            "--external-id",
            run.external_id,
            "--client",
            client_id,
            "--output-dir",
            str(report_dir),
            "--report-mode",
            "lead_magnet",
            "--check-identity",
            "--no-upload",
        ]
        if resolved_regions:
            command.extend(["--regions", *resolved_regions])
        if self._settings.run_collect_ce_resource_daily:
            command.append("--collect-ce-resource-daily")
        if self._settings.run_rate_limit_safe_mode:
            command.append("--rate-limit-safe-mode")
        return command

    def _resolved_regions_for_run(self, run: RunRecord) -> tuple[str, ...]:
        raw_regions = run.validation_payload.get("resolved_regions")
        if not isinstance(raw_regions, list):
            return self._settings.default_regions

        regions = tuple(
            str(region).strip()
            for region in raw_regions
            if isinstance(region, str) and region.strip()
        )
        return regions or self._settings.default_regions

    def _collect_artifact_paths(self, report_dir: Path) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        candidates = {
            "dashboard": report_dir / "dashboard.html",
            "appendix": report_dir / "appendix.html",
            "preview": report_dir / "out" / "index.html",
            "summary": report_dir / "out" / "summary.json",
            "bundle": report_dir / "out" / "report-bundle.zip",
            "access_report": report_dir / "access_report.json",
            "accounts": report_dir / "accounts.json",
            "exports_csv": report_dir / "exports.csv",
            "exports_json": report_dir / "exports.json",
            "exports_schema": report_dir / "exports.schema.json",
        }
        for kind, path in candidates.items():
            if path.exists():
                artifacts[kind] = path.relative_to(report_dir).as_posix()
        return artifacts

    def _read_account_id(self, report_dir: Path) -> str | None:
        access_report_path = report_dir / "access_report.json"
        if not access_report_path.exists():
            return None
        try:
            payload = json.loads(access_report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        account_id = payload.get("account_id")
        return str(account_id) if isinstance(account_id, str) and account_id else None

    def _friendly_failure_summary(self, stdout: str, stderr: str) -> str:
        combined_lines = [
            line.strip()
            for line in [*stderr.splitlines(), *stdout.splitlines()]
            if line.strip()
        ]
        if not combined_lines:
            return "The report run failed before returning a useful error message."
        final_line = combined_lines[-1]
        if "Failed to assume role" in final_line:
            return (
                "The report could not assume the AWS role. "
                "Double-check the role ARN, trust policy, and external ID."
            )
        return final_line

    def _build_process_log(self, command: list[str], stdout: str, stderr: str) -> str:
        log = (
            "$ " + " ".join(command) + "\n\n"
            "--- stdout ---\n"
            + stdout
            + "\n--- stderr ---\n"
            + stderr
        )
        return log[-20000:]

"""FastAPI app for the AWS Savings Review lead-magnet workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from finops_pack.api.emailer import EmailService
from finops_pack.api.runner import RunOrchestrator
from finops_pack.api.settings import WebSettings, load_web_settings
from finops_pack.api.storage import RunRecord, SQLiteLeadStore
from finops_pack.api.validation import (
    SubmissionValidator,
    ValidationResult,
    build_permissions_policy,
    build_trust_policy,
    generate_external_id,
)
from finops_pack.domain.models.assessment import AccountScopeType
from finops_pack.jobs.coordinator import JobCoordinator
from finops_pack.jobs.queue import SQLiteJobQueue

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class WebServices:
    """Shared services stored on the FastAPI app state."""

    settings: WebSettings
    store: SQLiteLeadStore
    validator: SubmissionValidator
    email_service: EmailService
    orchestrator: RunOrchestrator
    job_coordinator: JobCoordinator
    templates: Jinja2Templates


def create_app(
    settings: WebSettings | None = None,
    *,
    store: SQLiteLeadStore | None = None,
    validator: SubmissionValidator | None = None,
    email_service: EmailService | None = None,
    orchestrator: RunOrchestrator | None = None,
    job_coordinator: JobCoordinator | None = None,
) -> FastAPI:
    """Create the FastAPI application."""
    resolved_settings = settings or load_web_settings()
    resolved_store = store or SQLiteLeadStore(resolved_settings.database_path)
    resolved_store.initialize()
    resolved_job_queue = SQLiteJobQueue(resolved_settings.database_path)
    resolved_job_queue.initialize()
    resolved_settings.data_dir.mkdir(parents=True, exist_ok=True)
    resolved_settings.runs_dir.mkdir(parents=True, exist_ok=True)

    resolved_validator = validator or SubmissionValidator(resolved_settings)
    resolved_email_service = email_service or EmailService(resolved_settings)
    resolved_orchestrator = orchestrator or RunOrchestrator(
        resolved_settings,
        resolved_store,
        resolved_email_service,
    )
    resolved_job_coordinator = job_coordinator or JobCoordinator(resolved_job_queue)
    templates = Jinja2Templates(directory=str(resolved_settings.template_dir))

    app = FastAPI(title=resolved_settings.app_name)
    app.mount(
        "/site-static",
        StaticFiles(directory=str(resolved_settings.static_dir)),
        name="site_static",
    )
    app.state.services = WebServices(
        settings=resolved_settings,
        store=resolved_store,
        validator=resolved_validator,
        email_service=resolved_email_service,
        orchestrator=resolved_orchestrator,
        job_coordinator=resolved_job_coordinator,
        templates=templates,
    )

    def render(
        request: Request,
        template_name: str,
        *,
        page_title: str,
        **context: Any,
    ) -> HTMLResponse:
        services = _services(request)
        return services.templates.TemplateResponse(
            request,
            template_name,
            {
                "request": request,
                "page_title": page_title,
                "settings": services.settings,
                **context,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def landing_page(request: Request) -> HTMLResponse:
        services = _services(request)
        return render(
            request,
            "landing.html",
            page_title="AWS Savings Review",
            trusted_account_id=services.settings.operator_trusted_account_id,
        )

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(
        request: Request,
        company_name: str | None = None,
        external_id: str | None = None,
    ) -> HTMLResponse:
        services = _services(request)
        suggested_external_id = external_id or generate_external_id(company_name)
        return render(
            request,
            "setup.html",
            page_title="Create a read-only AWS role",
            suggested_external_id=suggested_external_id,
            company_name=company_name or "",
            trust_policy=build_trust_policy(
                trusted_account_id=services.settings.operator_trusted_account_id,
                external_id=suggested_external_id,
            ),
            permissions_policy=build_permissions_policy(),
        )

    @app.get("/intake", response_class=HTMLResponse, name="intake_page")
    def intake_page(
        request: Request,
        external_id: str | None = None,
        company_name: str | None = None,
    ) -> HTMLResponse:
        return render(
            request,
            "intake.html",
            page_title="Connect your AWS account",
            form_data=_build_intake_form_data(
                company_name=company_name or "",
                external_id=external_id or "",
            ),
            errors=[],
            validation_result=None,
        )

    @app.get("/submit", response_class=HTMLResponse, name="submission_page")
    def submission_page(
        request: Request,
        external_id: str | None = None,
        company_name: str | None = None,
    ) -> HTMLResponse:
        return intake_page(
            request,
            external_id=external_id,
            company_name=company_name,
        )

    @app.post("/intake", response_class=HTMLResponse, name="submit_intake")
    def submit_intake(
        request: Request,
        background_tasks: BackgroundTasks,
        company_name: str = Form(default=""),
        contact_name: str = Form(default=""),
        email: str = Form(...),
        account_scope: str = Form(default=AccountScopeType.SINGLE_ACCOUNT.value),
        role_arn: str = Form(...),
        external_id: str = Form(...),
        cost_explorer_enabled: str | None = Form(default=None),
        cost_optimization_hub_enabled: str | None = Form(default=None),
        notes: str = Form(default=""),
    ) -> Response:
        services = _services(request)
        scope = AccountScopeType.from_form_value(account_scope)
        normalized_email = email.strip().lower()
        form_data = _build_intake_form_data(
            company_name=company_name,
            contact_name=contact_name,
            email=normalized_email,
            account_scope=scope.value,
            role_arn=role_arn,
            external_id=external_id,
            notes=notes,
            cost_explorer_enabled=bool(cost_explorer_enabled),
            cost_optimization_hub_enabled=bool(cost_optimization_hub_enabled),
        )

        form_errors: list[str] = []
        if not EMAIL_RE.match(normalized_email):
            form_errors.append("Enter a valid work email address.")

        if form_errors:
            return render(
                request,
                "intake.html",
                page_title="Connect your AWS account",
                form_data=form_data,
                errors=form_errors,
                validation_result=None,
            )

        validation_result = services.validator.validate_submission(
            role_arn=role_arn,
            external_id=external_id,
            confirmed_cost_explorer=bool(cost_explorer_enabled),
            confirmed_cost_optimization_hub=bool(cost_optimization_hub_enabled),
            account_scope=scope,
        )
        if not validation_result.can_proceed:
            return render(
                request,
                "intake.html",
                page_title="Connect your AWS account",
                form_data=form_data,
                errors=list(validation_result.blocking_issues),
                validation_result=validation_result,
            )

        lead = services.store.create_or_update_lead(
            email=normalized_email,
            company_name=company_name.strip() or None,
            contact_name=contact_name.strip() or None,
        )
        run = services.store.create_validated_run_draft(
            role_arn=role_arn.strip(),
            external_id=external_id.strip(),
            generated_external_id=external_id.strip(),
            company_name=company_name.strip() or None,
            contact_name=contact_name.strip() or None,
            notes=notes.strip() or None,
            validation_payload=validation_result.to_payload(),
            account_scope=scope.value,
            status="QUEUED",
        )
        services.store.attach_lead_to_run(run_public_id=run.public_id, lead_id=lead.id)
        queued_run = _load_run_or_404(services, run.public_id)
        services.job_coordinator.enqueue_assessment(
            run_public_id=run.public_id,
            account_scope=scope,
        )
        background_tasks.add_task(
            services.email_service.send_lead_confirmation,
            lead,
            queued_run,
        )
        background_tasks.add_task(
            services.email_service.send_internal_submission_notification,
            lead,
            queued_run,
        )
        return RedirectResponse(
            request.url_for("run_status_page", run_public_id=run.public_id),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.get("/runs/{run_public_id}", response_class=HTMLResponse, name="run_status_page")
    def run_status_page(request: Request, run_public_id: str) -> HTMLResponse:
        services = _services(request)
        run = _load_run_or_404(services, run_public_id)
        lead = (
            services.store.get_lead_by_public_id(run.lead_public_id)
            if run.lead_public_id
            else None
        )
        validation_result = ValidationResult.from_payload(run.validation_payload)
        return render(
            request,
            "run_status.html",
            page_title="AWS Savings Review",
            run=run,
            lead=lead,
            validation_result=validation_result,
            dashboard_url=_artifact_url(run, "dashboard_path"),
            appendix_url=_artifact_url(run, "appendix_path"),
            bundle_url=_artifact_for_kind(run, "bundle"),
            history_url=(
                request.url_for("lead_history_page", lead_public_id=lead.public_id)
                if lead is not None
                else None
            ),
        )

    @app.get("/runs/{run_public_id}/status", response_class=JSONResponse)
    def run_status_json(request: Request, run_public_id: str) -> JSONResponse:
        services = _services(request)
        run = _load_run_or_404(services, run_public_id)
        return JSONResponse(
            {
                "status": run.status,
                "error_summary": run.error_summary,
                "dashboard_url": _artifact_url(run, "dashboard_path"),
                "result_url": str(
                    request.url_for("run_status_page", run_public_id=run_public_id)
                ),
            }
        )

    @app.get("/history/{lead_public_id}", response_class=HTMLResponse, name="lead_history_page")
    def lead_history_page(request: Request, lead_public_id: str) -> HTMLResponse:
        services = _services(request)
        lead = services.store.get_lead_by_public_id(lead_public_id)
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        runs = services.store.list_runs_for_lead_public_id(lead_public_id)
        return render(
            request,
            "history.html",
            page_title="Report history",
            lead=lead,
            runs=runs,
        )

    @app.get("/artifacts/{run_public_id}/{artifact_path:path}")
    def artifact_download(run_public_id: str, artifact_path: str, request: Request) -> FileResponse:
        services = _services(request)
        run = _load_run_or_404(services, run_public_id)
        if run.report_dir is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        report_dir = Path(run.report_dir).resolve()
        target = (report_dir / artifact_path).resolve()
        if report_dir not in target.parents and target != report_dir:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(target)

    @app.get("/healthz", response_class=JSONResponse)
    def healthcheck() -> JSONResponse:
        return JSONResponse({"ok": True})

    return app


def _services(request: Request) -> WebServices:
    return cast(WebServices, request.app.state.services)


def _load_run_or_404(services: WebServices, run_public_id: str) -> RunRecord:
    run = services.store.get_run_by_public_id(run_public_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return run


def _artifact_url(run: RunRecord, attribute_name: str) -> str | None:
    relative_path = getattr(run, attribute_name)
    if not isinstance(relative_path, str) or not relative_path:
        return None
    return f"/artifacts/{run.public_id}/{relative_path}"


def _artifact_for_kind(run: RunRecord, kind: str) -> str | None:
    for artifact in run.artifacts:
        if artifact.kind == kind:
            return f"/artifacts/{run.public_id}/{artifact.relative_path}"
    return None


def _build_intake_form_data(
    *,
    company_name: str = "",
    contact_name: str = "",
    email: str = "",
    account_scope: str = "single_account",
    role_arn: str = "",
    external_id: str = "",
    notes: str = "",
    cost_explorer_enabled: bool = False,
    cost_optimization_hub_enabled: bool = False,
) -> dict[str, str | bool]:
    return {
        "company_name": company_name,
        "contact_name": contact_name,
        "email": email,
        "account_scope": account_scope,
        "role_arn": role_arn,
        "external_id": external_id,
        "notes": notes,
        "cost_explorer_enabled": cost_explorer_enabled,
        "cost_optimization_hub_enabled": cost_optimization_hub_enabled,
    }


def main() -> None:
    """Run the FastAPI app under uvicorn."""
    settings = load_web_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.web_host,
        port=settings.web_port,
    )

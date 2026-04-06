"""Simple SMTP email delivery for lead confirmations and notifications."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from finops_pack.api.settings import WebSettings
from finops_pack.api.storage import LeadRecord, RunRecord


class EmailService:
    """Send confirmation and notification emails when SMTP is configured."""

    def __init__(self, settings: WebSettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        """Return True when outbound email is configured."""
        return bool(self._settings.smtp_host and self._settings.from_email)

    def send_lead_confirmation(self, lead: LeadRecord, run: RunRecord) -> None:
        """Send a confirmation email right after the lead submits their email."""
        if not self.enabled:
            return
        subject = f"{self._settings.brand_name}: we started your AWS savings review"
        body = (
            f"Hi{self._name_suffix(lead.contact_name)},\n\n"
            f"We have started your AWS savings review for "
            f"{lead.company_name or 'your account'}.\n\n"
            f"Live status: {self._run_url(run)}\n"
            f"Report history: {self._history_url(lead)}\n\n"
            "You do not need to send any AWS password, console login, or access keys.\n"
            "This run uses the read-only cross-account role you provided.\n\n"
            f"{self._settings.brand_name}"
        )
        self._send_message(to_email=lead.email, subject=subject, body=body)

    def send_lead_report_ready(self, lead: LeadRecord, run: RunRecord) -> None:
        """Send the finished report link to the lead."""
        if not self.enabled:
            return
        subject = f"{self._settings.brand_name}: your AWS savings report is ready"
        body = (
            f"Hi{self._name_suffix(lead.contact_name)},\n\n"
            "Your AWS savings report is ready.\n\n"
            f"Open the report: {self._run_url(run)}\n"
            f"Prior reports: {self._history_url(lead)}\n\n"
            f"If you would like help implementing the highest-value actions, "
            f"book a review here: {self._settings.report_cta_url}\n\n"
            f"{self._settings.brand_name}"
        )
        self._send_message(to_email=lead.email, subject=subject, body=body)

    def send_internal_submission_notification(self, lead: LeadRecord, run: RunRecord) -> None:
        """Notify the internal team when a new lead completes the email step."""
        if not self.enabled or not self._settings.notification_email:
            return
        subject = f"New AWS Savings Review lead: {lead.company_name or lead.email}"
        body = (
            "A new AWS Savings Review lead completed setup.\n\n"
            f"Lead email: {lead.email}\n"
            f"Company: {lead.company_name or '-'}\n"
            f"Contact: {lead.contact_name or '-'}\n"
            f"Role ARN: {run.role_arn}\n"
            f"Run status: {run.status}\n"
            f"Report history: {self._history_url(lead)}\n"
            f"Run page: {self._run_url(run)}\n"
        )
        self._send_message(
            to_email=self._settings.notification_email,
            subject=subject,
            body=body,
        )

    def send_internal_report_ready(self, lead: LeadRecord | None, run: RunRecord) -> None:
        """Notify the internal team when a report finishes successfully."""
        if not self.enabled or not self._settings.notification_email:
            return
        subject = f"Report ready: {lead.company_name if lead else run.public_id}"
        body = (
            "An AWS Savings Review run finished successfully.\n\n"
            f"Lead email: {lead.email if lead else '-'}\n"
            f"Company: {lead.company_name if lead and lead.company_name else '-'}\n"
            f"AWS account: {run.account_id or '-'}\n"
            f"Run page: {self._run_url(run)}\n"
            f"Report history: {self._history_url(lead) if lead else '-'}\n"
        )
        self._send_message(
            to_email=self._settings.notification_email,
            subject=subject,
            body=body,
        )

    def send_internal_run_failed(self, lead: LeadRecord | None, run: RunRecord) -> None:
        """Notify the internal team when a run fails after the email step."""
        if not self.enabled or not self._settings.notification_email:
            return
        subject = (
            "Run failed: "
            f"{lead.company_name if lead and lead.company_name else run.public_id}"
        )
        body = (
            "An AWS Savings Review run failed.\n\n"
            f"Lead email: {lead.email if lead else '-'}\n"
            f"Company: {lead.company_name if lead and lead.company_name else '-'}\n"
            f"Run page: {self._run_url(run)}\n"
            f"Friendly error: {run.error_summary or 'No summary captured.'}\n"
        )
        self._send_message(
            to_email=self._settings.notification_email,
            subject=subject,
            body=body,
        )

    def _send_message(self, *, to_email: str, subject: str, body: str) -> None:
        if not self._settings.smtp_host or not self._settings.from_email:
            return
        message = EmailMessage()
        message["From"] = self._settings.from_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(self._settings.smtp_host, self._settings.smtp_port) as smtp:
            if self._settings.smtp_use_tls:
                smtp.starttls()
            if self._settings.smtp_username and self._settings.smtp_password:
                smtp.login(
                    self._settings.smtp_username,
                    self._settings.smtp_password,
                )
            smtp.send_message(message)

    def _run_url(self, run: RunRecord) -> str:
        return f"{self._settings.base_url}/runs/{run.public_id}"

    def _history_url(self, lead: LeadRecord | None) -> str:
        if lead is None:
            return self._settings.base_url
        return f"{self._settings.base_url}/history/{lead.public_id}"

    def _name_suffix(self, name: str | None) -> str:
        return f" {name}" if name else ""

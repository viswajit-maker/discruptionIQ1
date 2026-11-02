"""
Script to send a test approval pending notification email via Resend.

Run with:
    python -m app.scripts.send_test_email
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
import httpx

from app.core.config import get_settings
from app.services.email_service import (
    build_approval_email_html,
    build_approval_email_text,
    _fmt_currency,
)


def main() -> int:
    settings = get_settings()

    print("==================================================")
    print("DisruptionIQ — Resend Test Email Dispatcher")
    print("==================================================")

    if not settings.resend_api_key:
        print("[ERROR] RESEND_API_KEY is not configured in environment / .env")
        return 1

    if not settings.approver_email:
        print("[ERROR] APPROVER_EMAIL is not configured in environment / .env")
        return 1

    masked_key = (
        settings.resend_api_key[:6] + "..." + settings.resend_api_key[-4:]
        if len(settings.resend_api_key) > 10
        else "***"
    )
    print(f"RESEND_API_KEY : {masked_key} (configured)")
    print(f"APPROVER_EMAIL : {settings.approver_email}")
    print(f"EMAIL_FROM     : {settings.email_from}")
    print(f"APP_BASE_URL   : {settings.app_base_url}")
    print("--------------------------------------------------")

    sample_request_id = f"REQ-TEST-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    sample_plan_name = "Frankfurt Hub Dual-Sourcing Expedite"
    sample_strategy = "Multi-Hub Rebalance & Expedite"
    sample_disruption_title = "Port Congestion - Singapore Transshipment Hub"
    sample_disruption_id = "DISR-2026-0042"
    sample_submitted_by = "analyst@disruptioniq.local"
    sample_submitted_at = datetime.now(timezone.utc)
    sample_cost = 62500.00
    sample_orders_protected = 14
    sample_savings = 93750.00
    sample_justification = (
        "Re-route 14 critical Tier-1 customer shipments via air freight expedite from Frankfurt. "
        "Avoids acute factory shutdown penalties and preserves key SLAs across EU automotive contracts."
    )

    threshold = settings.procurement_approval_threshold_usd
    show_authority = sample_cost > threshold

    common_kwargs = dict(
        request_id=sample_request_id,
        plan_name=sample_plan_name,
        strategy_type=sample_strategy,
        disruption_title=sample_disruption_title,
        disruption_id=sample_disruption_id,
        submitted_by=sample_submitted_by,
        submitted_at=sample_submitted_at,
        estimated_cost_usd=sample_cost,
        orders_protected_count=sample_orders_protected,
        projected_savings_usd=sample_savings,
        justification=sample_justification,
        app_base_url=settings.app_base_url,
        show_authority_note=show_authority,
        approval_threshold_usd=threshold,
    )

    html_content = build_approval_email_html(**common_kwargs)
    text_content = build_approval_email_text(**common_kwargs)
    subject = f"Approval pending: {sample_strategy} · {sample_request_id} · {_fmt_currency(sample_cost)}"

    payload = {
        "from": settings.email_from,
        "to": [settings.approver_email],
        "subject": subject,
        "html": html_content,
        "text": text_content,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": sample_request_id,
    }

    print(f"Sending test email for Request ID: {sample_request_id} ...")
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post("https://api.resend.com/emails", json=payload, headers=headers)

        print(f"Resend HTTP Status Code: {response.status_code}")
        try:
            data = response.json()
        except Exception:
            data = {}

        if response.is_success:
            msg_id = data.get("id", "N/A")
            print(f"Resend Message ID       : {msg_id}")
            print("Status                  : SUCCESS")
            print("==================================================")
            return 0
        else:
            print(f"[ERROR] Resend API Error: {response.text}")
            print("==================================================")
            return 1
    except Exception as exc:
        print(f"[EXCEPTION] Failed to send email: {type(exc).__name__}: {exc}")
        print("==================================================")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""
Email notification service for DisruptionIQ.

Sends an informational approval-pending email via the Resend REST API using httpx.
No approval or rejection actions are embedded in the email.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"

# ─────────────────────────────────────────────────────────────────────────────
# Currency & text helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_currency(value: float) -> str:
    """Format a float as $1,234,567.89 with thousands separators."""
    return f"${value:,.2f}"


def _esc(value: object) -> str:
    """HTML-escape any value, converting to string first."""
    return html.escape(str(value) if value is not None else "")


def _fmt_dt(dt: Optional[datetime]) -> str:
    """Format a datetime as 'Oct 31, 2026 at 12:34 UTC'."""
    if dt is None:
        return "—"
    return dt.strftime("%b %d, %Y at %H:%M UTC")


# ─────────────────────────────────────────────────────────────────────────────
# HTML template
# ─────────────────────────────────────────────────────────────────────────────

def build_approval_email_html(
    *,
    request_id: str,
    plan_name: str,
    strategy_type: str,
    disruption_title: str,
    disruption_id: str,
    submitted_by: str,
    submitted_at: Optional[datetime],
    estimated_cost_usd: float,
    orders_protected_count: int,
    projected_savings_usd: float,
    justification: str,
    app_base_url: str,
    show_authority_note: bool = False,
    approval_threshold_usd: float = 50_000.0,
) -> str:
    """Return a complete HTML email document (table-based, inline styles)."""

    # Safely escape all user-supplied content
    e_request_id = _esc(request_id)
    e_plan_name = _esc(plan_name)
    e_strategy = _esc(strategy_type)
    e_dis_title = _esc(disruption_title)
    e_dis_id = _esc(disruption_id)
    e_submitted_by = _esc(submitted_by)
    e_submitted_at = _esc(_fmt_dt(submitted_at))
    e_cost = _esc(_fmt_currency(estimated_cost_usd))
    e_savings = _esc(_fmt_currency(projected_savings_usd))
    e_justification = _esc(justification)
    e_base_url = _esc(app_base_url.rstrip("/"))

    authority_block = ""
    if show_authority_note:
        e_threshold = _esc(_fmt_currency(approval_threshold_usd))
        authority_block = f"""
        <tr>
          <td style="padding:0 0 20px 0;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
              <tr>
                <td bgcolor="#451a03" style="background-color:#451a03;border-left:4px solid #f59e0b;
                    border-radius:6px;padding:14px 18px;">
                  <p style="margin:0;font-size:13px;color:#fef3c7;line-height:1.5;">
                    <strong style="color:#fbbf24;">&#x26A0;&#xFE0F; Authority Required</strong><br>
                    Estimated cost ({e_cost}) exceeds your persona approval limit ({e_threshold}).
                    Requires Supply Chain Director.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>Approval Pending — DisruptionIQ</title>
  <!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
  <style>
    /* Force light-mode rendering even in dark-mode clients */
    :root {{ color-scheme: light; supported-color-schemes: light; }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:'Segoe UI',Arial,sans-serif;">
  <!-- Hidden preheader -->
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">
    {_esc(str(orders_protected_count))} customer SLAs at stake. Review in DisruptionIQ.
    &nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;
  </div>

  <!-- Outer wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f7"
         style="background-color:#f4f4f7;" role="presentation">
    <tr>
      <td align="center" style="padding:32px 16px;">

        <!-- Card -->
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;" role="presentation">

          <!-- ── HEADER ── -->
          <tr>
            <td bgcolor="#0f172a" style="background-color:#0f172a;border-radius:12px 12px 0 0;
                padding:28px 32px 24px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                <tr>
                  <td>
                    <p style="margin:0;font-size:22px;font-weight:700;color:#ffffff;letter-spacing:-0.5px;">
                      DisruptionIQ
                    </p>
                    <p style="margin:6px 0 0 0;font-size:13px;color:#94a3b8;">
                      Intelligent Supply Chain Operations Platform
                    </p>
                  </td>
                  <td align="right" valign="top">
                    <span style="display:inline-block;background-color:#f59e0b;color:#1c1107;
                          font-size:11px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;
                          padding:5px 12px;border-radius:20px;">
                      ● Pending Review
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── INTRO ── -->
          <tr>
            <td bgcolor="#1e293b" style="background-color:#1e293b;padding:28px 32px 20px 32px;">
              <p style="margin:0;font-size:17px;color:#e2e8f0;line-height:1.5;font-weight:500;">
                A recovery plan was submitted and is waiting for approval.
              </p>
            </td>
          </tr>

          <!-- ── DETAILS TABLE ── -->
          <tr>
            <td bgcolor="#1e293b" style="background-color:#1e293b;padding:0 32px 24px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     bgcolor="#0f172a" style="background-color:#0f172a;border-radius:8px;
                     border:1px solid #334155;" role="presentation">
                <tr>
                  <td style="padding:20px 24px;">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                      <tr>
                        <td style="padding-bottom:12px;">
                          <span style="font-size:11px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;">Request ID</span><br>
                          <span style="font-family:'Courier New',Courier,monospace;font-size:14px;
                                color:#a78bfa;font-weight:600;">{e_request_id}</span>
                        </td>
                        <td style="padding-bottom:12px;" align="right">
                          <span style="font-size:11px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;">Plan Type</span><br>
                          <span style="font-size:14px;color:#e2e8f0;font-weight:600;">{e_strategy}</span>
                        </td>
                      </tr>
                      <tr>
                        <td colspan="2" style="border-top:1px solid #1e293b;padding-top:12px;padding-bottom:12px;">
                          <span style="font-size:11px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;">Plan Name</span><br>
                          <span style="font-size:14px;color:#e2e8f0;font-weight:600;">{e_plan_name}</span>
                        </td>
                      </tr>
                      <tr>
                        <td colspan="2" style="border-top:1px solid #1e293b;padding-top:12px;
                            padding-bottom:12px;">
                          <span style="font-size:11px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;">Target Disruption</span><br>
                          <span style="font-size:14px;color:#e2e8f0;font-weight:600;">{e_dis_title}</span>
                          <span style="font-family:'Courier New',Courier,monospace;font-size:12px;
                                color:#64748b;margin-left:8px;">({e_dis_id})</span>
                        </td>
                      </tr>
                      <tr>
                        <td style="border-top:1px solid #1e293b;padding-top:12px;padding-bottom:4px;">
                          <span style="font-size:11px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;">Submitted By</span><br>
                          <span style="font-size:14px;color:#e2e8f0;">{e_submitted_by}</span>
                        </td>
                        <td align="right" style="border-top:1px solid #1e293b;padding-top:12px;
                            padding-bottom:4px;">
                          <span style="font-size:11px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;">Submitted</span><br>
                          <span style="font-size:13px;color:#94a3b8;">{e_submitted_at}</span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── METRICS ── -->
          <tr>
            <td bgcolor="#1e293b" style="background-color:#1e293b;padding:0 32px 24px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                <tr>
                  <!-- Expedite Expenditure -->
                  <td width="33%" style="padding-right:8px;">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0"
                           bgcolor="#0f172a" style="background-color:#0f172a;border-radius:8px;
                           border:1px solid #334155;" role="presentation">
                      <tr>
                        <td align="center" style="padding:18px 12px;">
                          <p style="margin:0;font-size:11px;color:#64748b;text-transform:uppercase;
                               letter-spacing:0.8px;">Expedite Expenditure</p>
                          <p style="margin:8px 0 0 0;font-size:22px;font-weight:700;color:#7c3aed;">
                            {e_cost}
                          </p>
                        </td>
                      </tr>
                    </table>
                  </td>
                  <!-- Orders Protected -->
                  <td width="33%" style="padding-right:8px;">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0"
                           bgcolor="#0f172a" style="background-color:#0f172a;border-radius:8px;
                           border:1px solid #334155;" role="presentation">
                      <tr>
                        <td align="center" style="padding:18px 12px;">
                          <p style="margin:0;font-size:11px;color:#64748b;text-transform:uppercase;
                               letter-spacing:0.8px;">Customer Orders Protected</p>
                          <p style="margin:8px 0 0 0;font-size:22px;font-weight:700;color:#10b981;">
                            {_esc(str(orders_protected_count))} SLAs
                          </p>
                        </td>
                      </tr>
                    </table>
                  </td>
                  <!-- Projected Savings -->
                  <td width="34%">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0"
                           bgcolor="#0f172a" style="background-color:#0f172a;border-radius:8px;
                           border:1px solid #334155;" role="presentation">
                      <tr>
                        <td align="center" style="padding:18px 12px;">
                          <p style="margin:0;font-size:11px;color:#64748b;text-transform:uppercase;
                               letter-spacing:0.8px;">Projected Penalty Avoidance</p>
                          <p style="margin:8px 0 0 0;font-size:22px;font-weight:700;color:#06b6d4;">
                            {e_savings}
                          </p>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── JUSTIFICATION ── -->
          <tr>
            <td bgcolor="#1e293b" style="background-color:#1e293b;padding:0 32px 24px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                <tr>
                  <td style="padding-bottom:8px;">
                    <p style="margin:0;font-size:12px;color:#64748b;text-transform:uppercase;
                         letter-spacing:0.8px;">Operational Evidence &amp; Justification</p>
                  </td>
                </tr>
                <tr>
                  <td bgcolor="#0f172a" style="background-color:#0f172a;border-radius:8px;
                      border:1px solid #334155;padding:16px 20px;">
                    <p style="margin:0;font-size:14px;color:#cbd5e1;line-height:1.6;">
                      {e_justification if e_justification else '<em style="color:#64748b;">No justification provided.</em>'}
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── AUTHORITY NOTE (conditional) ── -->
          {authority_block}

          <!-- ── CTA BUTTON ── -->
          <tr>
            <td bgcolor="#1e293b" style="background-color:#1e293b;padding:0 32px 32px 32px;"
                align="center">
              <table cellpadding="0" cellspacing="0" border="0" role="presentation">
                <tr>
                  <td bgcolor="#7c3aed" style="background-color:#7c3aed;border-radius:8px;">
                    <a href="{e_base_url}" target="_blank"
                       style="display:inline-block;padding:16px 40px;font-size:16px;font-weight:700;
                              color:#ffffff;text-decoration:none;letter-spacing:0.3px;">
                      Open Approval Inbox
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── FOOTER ── -->
          <tr>
            <td bgcolor="#0f172a" style="background-color:#0f172a;border-radius:0 0 12px 12px;
                padding:20px 32px;border-top:1px solid #1e293b;">
              <p style="margin:0;font-size:12px;color:#475569;text-align:center;line-height:1.6;">
                This is an automated notification from DisruptionIQ.<br>
                Approvals are completed in the app after sign-in.<br>
                Do not reply to this email.
              </p>
            </td>
          </tr>

        </table>
        <!-- /Card -->

      </td>
    </tr>
  </table>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Plain-text fallback
# ─────────────────────────────────────────────────────────────────────────────

def build_approval_email_text(
    *,
    request_id: str,
    plan_name: str,
    strategy_type: str,
    disruption_title: str,
    disruption_id: str,
    submitted_by: str,
    submitted_at: Optional[datetime],
    estimated_cost_usd: float,
    orders_protected_count: int,
    projected_savings_usd: float,
    justification: str,
    app_base_url: str,
    show_authority_note: bool = False,
    approval_threshold_usd: float = 50_000.0,
) -> str:
    """Return plain-text fallback for the approval notification email."""
    lines = [
        "DisruptionIQ — Approval Pending",
        "=" * 48,
        "",
        "A recovery plan was submitted and is waiting for approval.",
        "",
        f"Request ID     : {request_id}",
        f"Plan Name      : {plan_name}",
        f"Plan Type      : {strategy_type}",
        f"Disruption     : {disruption_title} ({disruption_id})",
        f"Submitted By   : {submitted_by}",
        f"Submitted At   : {_fmt_dt(submitted_at)}",
        "",
        "── Metrics ──────────────────────────────────",
        f"Expedite Expenditure      : {_fmt_currency(estimated_cost_usd)}",
        f"Customer Orders Protected : {orders_protected_count} SLAs",
        f"Projected Penalty Avoidance: {_fmt_currency(projected_savings_usd)}",
        "",
        "── Justification ────────────────────────────",
        justification or "(No justification provided.)",
        "",
    ]
    if show_authority_note:
        lines += [
            "── Authority Note ───────────────────────────",
            f"Estimated cost ({_fmt_currency(estimated_cost_usd)}) exceeds your persona "
            f"approval limit ({_fmt_currency(approval_threshold_usd)}). "
            "Requires Supply Chain Director.",
            "",
        ]
    lines += [
        f"Open Approval Inbox: {app_base_url.rstrip('/')}",
        "",
        "─" * 48,
        "This is an automated notification from DisruptionIQ.",
        "Approvals are completed in the app after sign-in.",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Send function
# ─────────────────────────────────────────────────────────────────────────────

def send_approval_notification(
    *,
    request_id: str,
    plan_id: str,
    plan_name: str,
    strategy_type: str,
    disruption_title: str,
    disruption_id: str,
    submitted_by: str,
    submitted_at: Optional[datetime],
    estimated_cost_usd: float,
    orders_protected_count: int,
    projected_savings_usd: float,
    justification: str,
) -> None:
    """
    Send an approval-pending notification email via Resend.

    This function is intended to be called as a FastAPI BackgroundTask.
    It never raises — on failure it logs a safe error and retries once.
    Idempotency key = request_id (prevents duplicates on double-clicks).
    """
    settings = get_settings()

    if not settings.resend_api_key:
        logger.warning("email_service: RESEND_API_KEY not configured; skipping approval notification.")
        return
    if not settings.approver_email:
        logger.warning("email_service: APPROVER_EMAIL not configured; skipping approval notification.")
        return

    threshold = settings.procurement_approval_threshold_usd
    show_authority = estimated_cost_usd > threshold

    common_kwargs = dict(
        request_id=request_id,
        plan_name=plan_name,
        strategy_type=strategy_type,
        disruption_title=disruption_title,
        disruption_id=disruption_id,
        submitted_by=submitted_by,
        submitted_at=submitted_at,
        estimated_cost_usd=estimated_cost_usd,
        orders_protected_count=orders_protected_count,
        projected_savings_usd=projected_savings_usd,
        justification=justification,
        app_base_url=settings.app_base_url,
        show_authority_note=show_authority,
        approval_threshold_usd=threshold,
    )

    html_body = build_approval_email_html(**common_kwargs)
    text_body = build_approval_email_text(**common_kwargs)

    cost_str = _fmt_currency(estimated_cost_usd)
    subject = f"Approval pending: {strategy_type} · {request_id} · {cost_str}"

    payload = {
        "from": settings.email_from,
        "to": [settings.approver_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
        # Once-per-request idempotency; Resend deduplicates within 24 h
        "Idempotency-Key": request_id,
    }

    def _attempt() -> tuple[int, str]:
        """Make one POST request and return (status_code, message_id)."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(_RESEND_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return resp.status_code, data.get("id", "")

    try:
        status_code, msg_id = _attempt()
        logger.info(
            "email_service: approval notification sent for request_id=%s plan_id=%s "
            "resend_status=%d message_id=%s",
            request_id, plan_id, status_code, msg_id,
        )
    except Exception as exc:
        logger.warning(
            "email_service: first attempt failed for request_id=%s plan_id=%s error=%s — retrying once",
            request_id, plan_id, type(exc).__name__,
        )
        try:
            status_code, msg_id = _attempt()
            logger.info(
                "email_service: retry succeeded for request_id=%s plan_id=%s "
                "resend_status=%d message_id=%s",
                request_id, plan_id, status_code, msg_id,
            )
        except Exception as exc2:
            logger.error(
                "email_service: approval notification FAILED after retry for request_id=%s plan_id=%s "
                "error_type=%s",
                request_id, plan_id, type(exc2).__name__,
            )

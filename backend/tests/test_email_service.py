"""
Unit tests for the Resend email notification service.

Tests HTML template generation, escaping, plain-text fallback,
authority note conditional rendering, and the send function with retry logic.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.email_service import (
    _fmt_currency,
    build_approval_email_html,
    build_approval_email_text,
    send_approval_notification,
)


@pytest.fixture
def sample_email_data():
    return {
        "request_id": "REQ-TEST-12345",
        "plan_name": "Frankfurt Hub Dual-Sourcing Expedite",
        "strategy_type": "Expedited Carrier Re-routing",
        "disruption_title": "Port Congestion - Singapore Transshipment Hub",
        "disruption_id": "DISR-2026-0042",
        "submitted_by": "analyst@disruptioniq.local",
        "submitted_at": datetime(2026, 10, 31, 14, 30, tzinfo=timezone.utc),
        "estimated_cost_usd": 62500.0,
        "orders_protected_count": 14,
        "projected_savings_usd": 93750.0,
        "justification": "Re-route 14 critical Tier-1 customer shipments to avoid acute SLA breach.",
        "app_base_url": "http://localhost:3000",
        "show_authority_note": False,
        "approval_threshold_usd": 50000.0,
    }


def test_fmt_currency():
    assert _fmt_currency(0.0) == "$0.00"
    assert _fmt_currency(1234.5) == "$1,234.50"
    assert _fmt_currency(1234567.89) == "$1,234,567.89"


def test_html_template_renders_all_fields(sample_email_data):
    html_out = build_approval_email_html(**sample_email_data)

    # Core identifiers and text
    assert "REQ-TEST-12345" in html_out
    assert "Expedited Carrier Re-routing" in html_out
    assert "Port Congestion - Singapore Transshipment Hub" in html_out
    assert "DISR-2026-0042" in html_out
    assert "analyst@disruptioniq.local" in html_out
    assert "Oct 31, 2026 at 14:30 UTC" in html_out

    # Metrics with currency formatting
    assert "$62,500.00" in html_out
    assert "14 SLAs" in html_out
    assert "$93,750.00" in html_out

    # Operational justification
    assert "Re-route 14 critical Tier-1 customer shipments" in html_out

    # Structure & Styling
    assert "DisruptionIQ" in html_out
    assert "Pending Review" in html_out
    assert "14 customer SLAs at stake. Review in DisruptionIQ." in html_out
    assert "Open Approval Inbox" in html_out
    assert 'href="http://localhost:3000"' in html_out
    assert "color-scheme" in html_out
    assert "600" in html_out  # max-width table
    assert "Approvals are completed in the app after sign-in." in html_out


def test_html_escaping_prevents_xss(sample_email_data):
    malicious_data = dict(sample_email_data)
    malicious_data["plan_name"] = "<script>alert('xss-plan')</script>"
    malicious_data["justification"] = "Malicious <img src=x onerror=alert(1)> & test 'quote' \"double\""
    malicious_data["disruption_title"] = "<b>Malicious Disruption</b>"
    malicious_data["submitted_by"] = "<user@malicious.com>"

    html_out = build_approval_email_html(**malicious_data)

    # Raw script or img tags should NOT be present
    assert "<script>" not in html_out
    assert "<img src=x" not in html_out
    assert "<b>Malicious" not in html_out

    # HTML escaped entities should be present
    assert "&lt;script&gt;alert(&#x27;xss-plan&#x27;)&lt;/script&gt;" in html_out or "&lt;script&gt;" in html_out
    assert "&lt;img src=x" in html_out
    assert "&amp;" in html_out
    assert "&lt;b&gt;Malicious Disruption&lt;/b&gt;" in html_out
    assert "&lt;user@malicious.com&gt;" in html_out


def test_authority_note_rendering(sample_email_data):
    # Without authority note
    sample_email_data["show_authority_note"] = False
    html_without = build_approval_email_html(**sample_email_data)
    text_without = build_approval_email_text(**sample_email_data)
    assert "Authority Required" not in html_without
    assert "Authority Note" not in text_without

    # With authority note
    sample_email_data["show_authority_note"] = True
    sample_email_data["estimated_cost_usd"] = 75000.0
    sample_email_data["approval_threshold_usd"] = 50000.0
    html_with = build_approval_email_html(**sample_email_data)
    text_with = build_approval_email_text(**sample_email_data)

    assert "Authority Required" in html_with
    assert "Estimated cost ($75,000.00) exceeds your persona approval limit ($50,000.00)." in html_with
    assert "Requires Supply Chain Director." in html_with

    assert "── Authority Note ──" in text_with
    assert "Estimated cost ($75,000.00) exceeds your persona approval limit ($50,000.00)." in text_with
    assert "Requires Supply Chain Director." in text_with


def test_plain_text_fallback(sample_email_data):
    text_out = build_approval_email_text(**sample_email_data)

    assert "DisruptionIQ — Approval Pending" in text_out
    assert "REQ-TEST-12345" in text_out
    assert "Expedited Carrier Re-routing" in text_out
    assert "Port Congestion - Singapore Transshipment Hub" in text_out
    assert "DISR-2026-0042" in text_out
    assert "$62,500.00" in text_out
    assert "14 SLAs" in text_out
    assert "$93,750.00" in text_out
    assert "http://localhost:3000" in text_out
    assert "Approvals are completed in the app after sign-in." in text_out


@patch("app.services.email_service.get_settings")
def test_send_skips_when_api_key_missing(mock_settings, sample_email_data):
    mock_settings.return_value = Settings(
        resend_api_key=None,
        approver_email="approver@example.com",
    )
    # Should safely return without error and without making HTTP calls
    with patch("httpx.Client") as mock_client:
        send_approval_notification(
            request_id="REQ-1",
            plan_id="PLAN-1",
            plan_name="Test Plan",
            strategy_type="Expedite",
            disruption_title="Test Disruption",
            disruption_id="DIS-1",
            submitted_by="analyst",
            submitted_at=datetime.now(timezone.utc),
            estimated_cost_usd=1000.0,
            orders_protected_count=5,
            projected_savings_usd=1500.0,
            justification="Test",
        )
        mock_client.assert_not_called()


@patch("app.services.email_service.get_settings")
def test_send_skips_when_approver_email_missing(mock_settings):
    mock_settings.return_value = Settings(
        resend_api_key="re_test_key",
        approver_email=None,
    )
    with patch("httpx.Client") as mock_client:
        send_approval_notification(
            request_id="REQ-1",
            plan_id="PLAN-1",
            plan_name="Test Plan",
            strategy_type="Expedite",
            disruption_title="Test Disruption",
            disruption_id="DIS-1",
            submitted_by="analyst",
            submitted_at=datetime.now(timezone.utc),
            estimated_cost_usd=1000.0,
            orders_protected_count=5,
            projected_savings_usd=1500.0,
            justification="Test",
        )
        mock_client.assert_not_called()


@patch("app.services.email_service.get_settings")
def test_send_approval_notification_retries_once_on_failure(mock_settings):
    mock_settings.return_value = Settings(
        resend_api_key="re_mock_key",
        approver_email="approver@example.com",
        email_from="onboarding@resend.dev",
        app_base_url="http://localhost:3000",
        procurement_approval_threshold_usd=50000.0,
    )

    with patch("httpx.Client") as mock_client_cls:
        client_instance = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = client_instance

        # First call fails, second call succeeds
        resp_fail = MagicMock()
        resp_fail.raise_for_status.side_effect = Exception("Network timeout")

        resp_success = MagicMock()
        resp_success.status_code = 200
        resp_success.raise_for_status.return_value = None
        resp_success.json.return_value = {"id": "msg_retry_123"}

        client_instance.post.side_effect = [resp_fail, resp_success]

        send_approval_notification(
            request_id="REQ-RETRY-01",
            plan_id="PLAN-1",
            plan_name="Test Plan",
            strategy_type="Expedite",
            disruption_title="Test Disruption",
            disruption_id="DIS-1",
            submitted_by="analyst",
            submitted_at=datetime.now(timezone.utc),
            estimated_cost_usd=1000.0,
            orders_protected_count=5,
            projected_savings_usd=1500.0,
            justification="Test",
        )

        assert client_instance.post.call_count == 2
        # Check idempotency key header was sent
        call_kwargs = client_instance.post.call_args[1]
        assert call_kwargs["headers"]["Idempotency-Key"] == "REQ-RETRY-01"
        assert call_kwargs["headers"]["Authorization"] == "Bearer re_mock_key"

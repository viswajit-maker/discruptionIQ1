"""
Recovery Arena API router with backend-enforced RBAC, separation of duties, and state machine validation.

Endpoints
---------
POST   /recovery/plans                      Create candidate plans for a disruption (plans:create)
GET    /recovery/plans/{plan_id}            Retrieve a single plan with allocations (plans:read)
GET    /recovery/{disruption_id}/plans      List all plans for a disruption (plans:read)
POST   /recovery/plans/{plan_id}/simulate   Simulate (enrich) a plan (plans:simulate)
GET    /recovery/{disruption_id}/compare    Compare all feasible plans (plans:read)
POST   /recovery/plans/{plan_id}/submit     Submit a plan for approval (plans:submit)
POST   /recovery/plans/{plan_id}/modify     Request modification / record notes (plans:modify)
POST   /recovery/plans/{plan_id}/approve    Approve a submitted plan (plans:approve_*)
POST   /recovery/plans/{plan_id}/reject     Reject a submitted plan (plans:reject)
POST   /recovery/plans/{plan_id}/execute    Execute an approved plan (plans:execute)
GET    /recovery/{disruption_id}/audit      Audit trail for a disruption's plans (audit:read)
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Permission, normalize_role, role_has_permission
from app.models.auth import User, UserRole
from app.models.governance import DisruptionImpact, DisruptionRecord
from app.models.recovery import PlanAuditEvent, RecoveryPlan
from app.services.email_service import send_approval_notification
from app.services.plan_executor import (
    ExecutionError,
    approve_plan,
    execute_plan,
    reject_plan,
    request_plan_modification,
    submit_plan_for_approval,
)
from app.services.plan_simulator import compare_plans, create_plans, get_plan, list_plans, simulate_plan

recovery_router = APIRouter(prefix="/recovery", tags=["Recovery Arena"])
logger = logging.getLogger(__name__)
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Request / response bodies
# ---------------------------------------------------------------------------

class CreatePlansRequest(BaseModel):
    disruptionId: str = Field(..., description="Disruption to generate plans for")
    priorityWeights: Optional[dict] = Field(
        None,
        description="Override FairShare weights e.g. {\"P1\": 10, \"P2\": 5, \"P3\": 1}",
    )


class SubmitPlanRequest(BaseModel):
    justification: str = Field(..., min_length=10, description="Business justification for approval")


class ModifyPlanRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Modification request notes/reason")


class ApprovePlanRequest(BaseModel):
    comments: str = Field(default="", description="Reviewer comments")


class RejectPlanRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Rejection reason")


class ExecutePlanRequest(BaseModel):
    idempotencyKey: str = Field(
        ...,
        min_length=8,
        description="Unique caller-supplied key; re-submitting the same key returns the original result without re-executing",
    )


def _execution_error_to_http(e: ExecutionError) -> HTTPException:
    """Map deterministic domain ExecutionError to appropriate HTTP status codes."""
    if e.code in ("SEPARATION_OF_DUTIES_VIOLATION", "PROCUREMENT_APPROVAL_REQUIRED"):
        http_status = status.HTTP_403_FORBIDDEN
    elif e.code == "PLAN_NOT_FOUND":
        http_status = status.HTTP_404_NOT_FOUND
    elif e.code == "INVALID_INPUT":
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        http_status = status.HTTP_409_CONFLICT
    return HTTPException(status_code=http_status, detail={"code": e.code, "message": e.reason})


def _missing_impact_context(db: Session, disruption_id: str) -> dict:
    """Describe why a disruption cannot yet be planned without inventing demand."""
    record = db.get(DisruptionRecord, disruption_id)
    if record is None:
        return {"code": "DISRUPTION_NOT_FOUND", "message": f"Disruption {disruption_id} was not found."}
    impact_types = set(db.scalars(
        select(DisruptionImpact.entity_type).where(DisruptionImpact.disruption_id == disruption_id)
    ).all())
    mappings: list[str] = []
    if "shipment" not in impact_types:
        mappings.append("a validated affected shipment")
    if "sku" not in impact_types:
        mappings.append("one or more affected SKUs")
    if "order" not in impact_types:
        mappings.append("a directly affected order, or a shipment/SKU mapping that resolves affected orders")
    return {
        "code": "MISSING_IMPACT_CONTEXT",
        "message": "Recovery planning needs validated affected demand. Add " + ", ".join(mappings) + " in the disruption report, then try again.",
        "disruptionId": disruption_id,
        "availableMappings": sorted(impact_types),
        "missingMappings": mappings,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@recovery_router.post(
    "/plans",
    summary="Create candidate recovery plans",
    response_model=list,
    status_code=201,
)
def create_recovery_plans(
    body: CreatePlansRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> list:
    """Generate multiple distinct candidate recovery plans for the given disruption."""
    if not role_has_permission(current_user.role, Permission.PLANS_CREATE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:create'"},
        )

    plans = create_plans(
        db,
        disruption_id=body.disruptionId,
        actor_id=current_user.id,
        actor_role=current_user.role,
        priority_weights=body.priorityWeights,
    )
    if not plans:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_missing_impact_context(db, body.disruptionId),
        )
    return plans


@recovery_router.get(
    "/plans/{plan_id}",
    summary="Retrieve a single recovery plan",
    response_model=dict,
)
def get_recovery_plan(
    plan_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Return a single plan with all allocation details."""
    if not role_has_permission(current_user.role, Permission.PLANS_READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:read'"},
        )

    plan = get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    return plan


@recovery_router.get(
    "/{disruption_id}/plans",
    summary="List all plans for a disruption",
    response_model=list,
)
def list_recovery_plans(
    disruption_id: str,
    db: DbSession,
    current_user: CurrentUser,
    status: Optional[str] = Query(default=None, description="Filter by plan status"),
) -> list:
    """Return all candidate plans for a disruption, optionally filtered by status."""
    if not role_has_permission(current_user.role, Permission.PLANS_READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:read'"},
        )

    return list_plans(db, disruption_id, status)


@recovery_router.post(
    "/plans/{plan_id}/simulate",
    summary="Simulate a plan",
    response_model=dict,
)
def simulate_recovery_plan(
    plan_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Re-run feasibility checks and enrich the plan with simulation metadata."""
    if not role_has_permission(current_user.role, Permission.PLANS_SIMULATE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:simulate'"},
        )

    try:
        return simulate_plan(db, plan_id, current_user.id, current_user.role)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@recovery_router.get(
    "/{disruption_id}/compare",
    summary="Compare feasible plans",
    response_model=dict,
)
def compare_recovery_plans(
    disruption_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Compare all feasible plans for a disruption using the same metrics."""
    if not role_has_permission(current_user.role, Permission.PLANS_READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:read'"},
        )

    return compare_plans(db, disruption_id)


@recovery_router.post(
    "/plans/{plan_id}/submit",
    summary="Submit a plan for approval",
    response_model=dict,
)
def submit_for_approval(
    plan_id: str,
    body: SubmitPlanRequest,
    db: DbSession,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> dict:
    """Move plan to `submitted` status and write audit event `plan_submitted`."""
    if not role_has_permission(current_user.role, Permission.PLANS_SUBMIT):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:submit'"},
        )

    try:
        result = submit_plan_for_approval(
            db, plan_id, current_user.id, current_user.role, body.justification
        )
    except ExecutionError as e:
        raise _execution_error_to_http(e)

    # Queue informational notification email via background task (non-blocking)
    try:
        plan = db.get(RecoveryPlan, plan_id)
        if plan:
            disruption = db.get(DisruptionRecord, plan.disruption_id)
            disruption_title = disruption.title if disruption else f"Disruption {plan.disruption_id}"
            request_id = plan.approval_id or f"REQ-{plan.id}"
            cost = float(plan.estimated_cost_usd or 0.0)
            projected_savings = round(cost * 1.5, 2)
            background_tasks.add_task(
                send_approval_notification,
                request_id=request_id,
                plan_id=plan.id,
                plan_name=plan.name,
                strategy_type=plan.strategy_type,
                disruption_title=disruption_title,
                disruption_id=plan.disruption_id,
                submitted_by=plan.submitted_by or current_user.id,
                submitted_at=plan.updated_at or plan.created_at,
                estimated_cost_usd=cost,
                orders_protected_count=plan.orders_protected_count or 0,
                projected_savings_usd=projected_savings,
                justification=body.justification,
            )
    except Exception as exc:
        logger.warning("Failed to schedule approval notification email for plan %s: %s", plan_id, exc)

    return result


@recovery_router.post(
    "/plans/{plan_id}/modify",
    summary="Request plan modification",
    response_model=dict,
)
def modify_recovery_plan(
    plan_id: str,
    body: ModifyPlanRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Request modification for a plan, setting state to modification_requested and recording reason."""
    if not role_has_permission(current_user.role, Permission.PLANS_MODIFY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:modify'"},
        )

    try:
        return request_plan_modification(
            db, plan_id, current_user.id, current_user.role, body.reason
        )
    except ExecutionError as e:
        raise _execution_error_to_http(e)


@recovery_router.post(
    "/plans/{plan_id}/approve",
    summary="Approve a submitted plan",
    response_model=dict,
)
def approve_recovery_plan(
    plan_id: str,
    body: ApprovePlanRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Approve a submitted plan with backend separation of duties and procurement policy enforcement."""
    can_approve_std = role_has_permission(current_user.role, Permission.PLANS_APPROVE_STANDARD)
    can_approve_proc = role_has_permission(current_user.role, Permission.PLANS_APPROVE_PROCUREMENT)

    if not (can_approve_std or can_approve_proc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": f"Role '{current_user.role}' is not an authorized approver for recovery plans.",
            },
        )

    try:
        return approve_plan(
            db, plan_id, current_user.id, current_user.role, body.comments
        )
    except ExecutionError as e:
        raise _execution_error_to_http(e)


@recovery_router.post(
    "/plans/{plan_id}/reject",
    summary="Reject a submitted plan",
    response_model=dict,
)
def reject_recovery_plan(
    plan_id: str,
    body: RejectPlanRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Reject a submitted plan with mandatory rejection reason and audit event."""
    if not role_has_permission(current_user.role, Permission.PLANS_REJECT):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:reject'"},
        )

    try:
        return reject_plan(
            db, plan_id, current_user.id, current_user.role, body.reason
        )
    except ExecutionError as e:
        raise _execution_error_to_http(e)


@recovery_router.post(
    "/plans/{plan_id}/execute",
    summary="Execute an approved plan",
    response_model=dict,
)
def execute_recovery_plan(
    plan_id: str,
    body: ExecutePlanRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """Execute an approved plan with re-validation, row-level locking, and idempotency protection."""
    if not role_has_permission(current_user.role, Permission.PLANS_EXECUTE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'plans:execute'"},
        )

    try:
        return execute_plan(
            db, plan_id, current_user.id, current_user.role, body.idempotencyKey
        )
    except ExecutionError as e:
        raise _execution_error_to_http(e)


@recovery_router.get(
    "/{disruption_id}/audit",
    summary="Audit trail for a disruption's plans",
    response_model=list,
)
def get_audit_trail(
    disruption_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> list:
    """Return all plan audit events for a disruption in chronological order."""
    if not role_has_permission(current_user.role, Permission.AUDIT_READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": f"Role '{current_user.role}' lacks permission 'audit:read'"},
        )

    events = db.scalars(
        select(PlanAuditEvent)
        .where(PlanAuditEvent.disruption_id == disruption_id)
        .order_by(PlanAuditEvent.occurred_at.asc())
    ).all()
    return [
        {
            "id": e.id,
            "planId": e.plan_id,
            "disruptionId": e.disruption_id,
            "action": e.action,
            "actorId": e.actor_id,
            "actorRole": e.actor_role,
            "occurredAt": e.occurred_at.isoformat(),
            "payload": json.loads(e.payload_json or "{}"),
        }
        for e in events
    ]

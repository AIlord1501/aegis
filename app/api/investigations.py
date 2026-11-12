from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import select

from app.agents.graph import run_investigation
from app.api.deps import CurrentUser, DbSession
from app.models.incident import Incident
from app.models.investigation import AgentExecution, Approval, Investigation, InvestigationCheckpoint, ToolExecution
from app.models.service import Service
from app.schemas.investigation import AgentExecutionRead, ApprovalDecision, ApprovalRead, InvestigationRead

router = APIRouter(prefix="/api/v1", tags=["investigations"])


async def owned_investigation(investigation_id: int, owner_id: int, db: DbSession) -> Investigation:
    investigation = await db.scalar(select(Investigation).where(Investigation.id == investigation_id, Investigation.owner_id == owner_id))
    if investigation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Investigation not found")
    return investigation


@router.post("/incidents/{incident_id}/investigate", response_model=InvestigationRead, status_code=status.HTTP_202_ACCEPTED)
async def start_investigation(incident_id: int, background_tasks: BackgroundTasks, current_user: CurrentUser, db: DbSession) -> Investigation:
    incident = await db.scalar(select(Incident).join(Service).where(Incident.id == incident_id, Service.owner_id == current_user.id))
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    existing = await db.scalar(select(Investigation).where(Investigation.incident_id == incident_id, Investigation.owner_id == current_user.id, Investigation.status.in_(["queued", "running", "waiting_approval"])).order_by(Investigation.id.desc()))
    if existing:
        return existing
    from uuid import uuid4

    investigation = Investigation(owner_id=current_user.id, incident_id=incident_id, thread_id=f"aegis-investigation-{uuid4()}", status="queued", state_json={}, max_iterations=8)
    db.add(investigation)
    await db.commit()
    await db.refresh(investigation)
    background_tasks.add_task(run_investigation, investigation.id)
    return investigation


@router.get("/investigations", response_model=list[InvestigationRead])
async def list_investigations(current_user: CurrentUser, db: DbSession, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)) -> list[Investigation]:
    rows = await db.scalars(select(Investigation).where(Investigation.owner_id == current_user.id).order_by(Investigation.created_at.desc()).offset(offset).limit(limit))
    return list(rows)


@router.get("/investigations/{investigation_id}", response_model=InvestigationRead)
async def get_investigation(investigation_id: int, current_user: CurrentUser, db: DbSession) -> Investigation:
    return await owned_investigation(investigation_id, current_user.id, db)


@router.get("/investigations/{investigation_id}/agents", response_model=list[AgentExecutionRead])
async def list_agent_executions(investigation_id: int, current_user: CurrentUser, db: DbSession) -> list[AgentExecution]:
    await owned_investigation(investigation_id, current_user.id, db)
    rows = await db.scalars(select(AgentExecution).where(AgentExecution.investigation_id == investigation_id).order_by(AgentExecution.started_at))
    return list(rows)


@router.get("/investigations/{investigation_id}/timeline")
async def investigation_timeline(investigation_id: int, current_user: CurrentUser, db: DbSession) -> list[dict]:
    await owned_investigation(investigation_id, current_user.id, db)
    checkpoints = list((await db.scalars(select(InvestigationCheckpoint).where(InvestigationCheckpoint.investigation_id == investigation_id))).all())
    agents = list((await db.scalars(select(AgentExecution).where(AgentExecution.investigation_id == investigation_id))).all())
    tools = list((await db.scalars(select(ToolExecution).where(ToolExecution.investigation_id == investigation_id))).all())
    approvals = list((await db.scalars(select(Approval).where(Approval.investigation_id == investigation_id))).all())
    timeline = [
        *[{"type": "checkpoint", "node": item.node_name, "timestamp": item.created_at.isoformat(), "state": item.state_json} for item in checkpoints],
        *[{"type": "agent", "agent": item.agent_name, "status": item.status, "timestamp": item.started_at.isoformat(), "duration_ms": item.duration_ms} for item in agents],
        *[{"type": "tool", "tool": item.tool_name, "status": item.status, "timestamp": item.started_at.isoformat(), "duration_ms": item.duration_ms} for item in tools],
        *[{"type": "approval", "status": item.status, "timestamp": item.created_at.isoformat(), "reason": item.review_reason} for item in approvals],
    ]
    return sorted(timeline, key=lambda item: item["timestamp"])


@router.get("/approvals", response_model=list[ApprovalRead])
async def list_approvals(current_user: CurrentUser, db: DbSession, status_filter: str = Query(default="pending", alias="status")) -> list[Approval]:
    if status_filter not in {"pending", "approved", "rejected", "all"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid approval status")
    query = select(Approval).where(Approval.owner_id == current_user.id).order_by(Approval.created_at.desc())
    if status_filter != "all":
        query = query.where(Approval.status == status_filter)
    rows = await db.scalars(query)
    return list(rows)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRead)
async def approve(approval_id: int, payload: ApprovalDecision, background_tasks: BackgroundTasks, current_user: CurrentUser, db: DbSession) -> Approval:
    approval = await db.scalar(select(Approval).where(Approval.id == approval_id, Approval.owner_id == current_user.id))
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval has already been reviewed")
    approval.status = "approved"
    approval.reviewed_by = current_user.id
    approval.review_reason = payload.reason
    approval.reviewed_at = datetime.now(timezone.utc)
    investigation = await db.get(Investigation, approval.investigation_id)
    if investigation:
        investigation.status = "queued"
    await db.commit()
    if investigation:
        background_tasks.add_task(run_investigation, investigation.id, resume_from="verification")
    return approval


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalRead)
async def reject(approval_id: int, payload: ApprovalDecision, current_user: CurrentUser, db: DbSession) -> Approval:
    approval = await db.scalar(select(Approval).where(Approval.id == approval_id, Approval.owner_id == current_user.id))
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval has already been reviewed")
    approval.status = "rejected"
    approval.reviewed_by = current_user.id
    approval.review_reason = payload.reason
    approval.reviewed_at = datetime.now(timezone.utc)
    investigation = await db.get(Investigation, approval.investigation_id)
    if investigation:
        investigation.status = "rejected"
        investigation.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return approval

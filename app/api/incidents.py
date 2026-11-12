from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.incident import Incident
from app.models.service import Service
from app.schemas.incident import IncidentCreate, IncidentRead

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
async def create_incident(payload: IncidentCreate, current_user: CurrentUser, db: DbSession) -> Incident:
    service = await db.scalar(select(Service).where(Service.id == payload.service_id, Service.owner_id == current_user.id))
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    incident = Incident(**payload.model_dump())
    db.add(incident)
    await db.commit()
    await db.refresh(incident)
    return incident


@router.get("", response_model=list[IncidentRead])
async def list_incidents(current_user: CurrentUser, db: DbSession) -> list[Incident]:
    result = await db.scalars(
        select(Incident).join(Service).where(Service.owner_id == current_user.id).order_by(Incident.id)
    )
    return list(result)


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(incident_id: int, current_user: CurrentUser, db: DbSession) -> Incident:
    incident = await db.scalar(
        select(Incident).join(Service).where(Incident.id == incident_id, Service.owner_id == current_user.id)
    )
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return incident

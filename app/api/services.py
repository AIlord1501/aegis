from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.api_keys import generate_ingest_api_key, hash_ingest_api_key
from app.models.service import Service
from app.schemas.service import ServiceCreate, ServiceCreated, ServiceRead

router = APIRouter(prefix="/services", tags=["services"])


@router.post("", response_model=ServiceCreated, status_code=status.HTTP_201_CREATED)
async def create_service(payload: ServiceCreate, current_user: CurrentUser, db: DbSession) -> dict[str, object]:
    ingest_api_key = generate_ingest_api_key()
    service = Service(
        owner_id=current_user.id,
        ingest_api_key_hash=hash_ingest_api_key(ingest_api_key),
        ingest_api_key_prefix=ingest_api_key[:16],
        **payload.model_dump(),
    )
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return {**ServiceRead.model_validate(service).model_dump(), "ingest_api_key": ingest_api_key}


@router.get("", response_model=list[ServiceRead])
async def list_services(current_user: CurrentUser, db: DbSession) -> list[Service]:
    result = await db.scalars(select(Service).where(Service.owner_id == current_user.id).order_by(Service.id))
    return list(result)


@router.get("/{service_id}", response_model=ServiceRead)
async def get_service(service_id: int, current_user: CurrentUser, db: DbSession) -> Service:
    service = await db.scalar(select(Service).where(Service.id == service_id, Service.owner_id == current_user.id))
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


@router.patch("/{service_id}", response_model=ServiceRead)
async def update_service(service_id: int, payload: ServiceCreate, current_user: CurrentUser, db: DbSession) -> Service:
    service = await db.scalar(select(Service).where(Service.id == service_id, Service.owner_id == current_user.id))
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    for key, value in payload.model_dump().items():
        setattr(service, key, value)
    await db.commit()
    await db.refresh(service)
    return service


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(service_id: int, current_user: CurrentUser, db: DbSession) -> None:
    service = await db.scalar(select(Service).where(Service.id == service_id, Service.owner_id == current_user.id))
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    await db.delete(service)
    await db.commit()


@router.post("/{service_id}/ingest-key", response_model=ServiceCreated)
async def rotate_ingest_key(service_id: int, current_user: CurrentUser, db: DbSession) -> dict[str, object]:
    service = await db.scalar(select(Service).where(Service.id == service_id, Service.owner_id == current_user.id))
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    ingest_api_key = generate_ingest_api_key()
    service.ingest_api_key_hash = hash_ingest_api_key(ingest_api_key)
    service.ingest_api_key_prefix = ingest_api_key[:16]
    await db.commit()
    await db.refresh(service)
    return {**ServiceRead.model_validate(service).model_dump(), "ingest_api_key": ingest_api_key}

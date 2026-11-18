import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis.exceptions import RedisError
from redis.asyncio import Redis
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.api_keys import api_key_matches
from app.core.config import get_settings
from app.core.fingerprint import event_fingerprint
from app.core.redis import get_redis
from app.models.service import Service
from app.schemas.events import EventBatch
from app.schemas.ingest import IngestResponse

router = APIRouter(prefix="/api/v1/events", tags=["events"])
settings = get_settings()
RedisDependency = Annotated[Redis, Depends(get_redis)]


async def authenticate_ingest_service(api_key: str, db: DbSession) -> Service:
    prefix = api_key[:16]
    services = await db.scalars(select(Service).where(Service.ingest_api_key_prefix == prefix))
    service = next((candidate for candidate in services if api_key_matches(api_key, candidate.ingest_api_key_hash)), None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingestion API key")
    return service


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(
    request: Request,
    payload: EventBatch,
    db: DbSession,
    redis: RedisDependency,
    x_api_key: str | None = Header(default=None),
) -> IngestResponse:
    if len(payload.events) > settings.event_max_batch_size:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Event batch is too large")
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key header is required")

    service = await authenticate_ingest_service(x_api_key, db)
    received_at = datetime.now(timezone.utc)
    messages: list[dict[str, str]] = []
    for event in payload.events:
        occurred_at = event.timestamp.astimezone(timezone.utc)
        fingerprint = event_fingerprint(
            service_id=service.id,
            event_id=event.event_id,
            occurred_at=occurred_at,
            severity=event.severity.value,
            source=event.source,
            message=event.message,
            attributes=event.attributes,
        )
        envelope = {
            "service_id": service.id,
            "event_id": event.event_id,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "received_at": received_at.isoformat().replace("+00:00", "Z"),
            "severity": event.severity.value,
            "source": event.source,
            "message": event.message,
            "attributes": event.attributes,
            "fingerprint": fingerprint,
        }
        messages.append({"payload": json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))})

    try:
        async with redis.pipeline(transaction=False) as pipeline:
            for message in messages:
                pipeline.xadd(settings.event_stream_key, message, maxlen=100_000, approximate=True)
            await pipeline.execute()
    except RedisError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Event queue unavailable") from exc

    return IngestResponse(accepted=len(messages), stream=settings.event_stream_key)

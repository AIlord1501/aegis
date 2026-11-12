from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, events, incidents, intelligence, investigations, knowledge, repositories, services
from app.core.config import get_settings
from app.core.redis import redis_client
from app.db.session import engine

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug)
app.include_router(auth.router)
app.include_router(services.router)
app.include_router(incidents.router)
app.include_router(events.router)
app.include_router(intelligence.router)
app.include_router(knowledge.router)
app.include_router(repositories.router)
app.include_router(investigations.router)


@app.middleware("http")
async def enforce_ingest_request_size(request: Request, call_next):
    if request.url.path == "/api/v1/events/ingest":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.event_max_request_bytes:
            return JSONResponse(status_code=413, content={"detail": "Ingestion request is too large"})
    return await call_next(request)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def readiness() -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await redis_client.ping()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Dependencies are unavailable") from exc
    return {"status": "ready"}

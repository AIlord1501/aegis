from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import Event, Service, User


@pytest.mark.asyncio
async def test_event_fingerprint_is_unique_in_storage():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        user = User(email="storage@example.com", password_hash="test")
        session.add(user)
        await session.flush()
        service = Service(owner_id=user.id, name="storage", ingest_api_key_hash="a" * 64, ingest_api_key_prefix="aegis_ingest_1")
        session.add(service)
        await session.flush()
        common = dict(
            service_id=service.id,
            occurred_at=datetime.now(timezone.utc),
            severity="error",
            source="api",
            message="failure",
            attributes={},
            fingerprint="f" * 64,
        )
        session.add(Event(**common))
        await session.commit()
        session.add(Event(**common))
        with pytest.raises(IntegrityError):
            await session.commit()
    await engine.dispose()

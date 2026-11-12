from datetime import datetime, timezone

import pytest

from app.core.redis import get_redis
from redis.exceptions import RedisError


class FakePipeline:
    def __init__(self, stream):
        self.stream = stream
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def xadd(self, stream, message, **_kwargs):
        self.messages.append((stream, message))
        return self

    async def execute(self):
        self.stream.extend(self.messages)
        return ["1-0"] * len(self.messages)


class FakeRedis:
    def __init__(self):
        self.stream = []

    def pipeline(self, transaction=False):
        assert transaction is False
        return FakePipeline(self.stream)


class FailingPipeline(FakePipeline):
    async def execute(self):
        raise RedisError("redis unavailable")


class FailingRedis(FakeRedis):
    def pipeline(self, transaction=False):
        assert transaction is False
        return FailingPipeline(self.stream)


async def register_and_create_service(client):
    await client.post("/auth/register", json={"email": "owner@example.com", "password": "correct horse battery staple"})
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": "correct horse battery staple"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    service = await client.post("/services", headers=headers, json={"name": "payments"})
    return service.json()


@pytest.mark.asyncio
async def test_ingest_accepts_structured_batch_and_publishes(client):
    fake_redis = FakeRedis()
    from app.main import app

    app.dependency_overrides[get_redis] = lambda: fake_redis
    service = await register_and_create_service(client)
    response = await client.post(
        "/api/v1/events/ingest",
        headers={"X-API-Key": service["ingest_api_key"]},
        json={
            "events": [
                {
                    "event_id": "evt-1",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "severity": "error",
                    "source": "payments-api",
                    "message": "database timeout",
                    "attributes": {"region": "ap-south-1"},
                }
            ]
        },
    )
    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert len(fake_redis.stream) == 1
    assert str(service["id"]) in fake_redis.stream[0][1]["payload"]


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_key_and_invalid_payload(client):
    fake_redis = FakeRedis()
    from app.main import app

    app.dependency_overrides[get_redis] = lambda: fake_redis
    service = await register_and_create_service(client)
    unauthorized = await client.post(
        "/api/v1/events/ingest",
        headers={"X-API-Key": "aegis_ingest_invalid"},
        json={"events": [{"timestamp": "2026-11-12T10:00:00Z", "severity": "info", "source": "api", "message": "bad"}]},
    )
    assert unauthorized.status_code == 401
    invalid = await client.post(
        "/api/v1/events/ingest",
        headers={"X-API-Key": service["ingest_api_key"]},
        json={"events": [{"timestamp": "2026-11-12T10:00:00", "severity": "nope", "source": "api", "message": "bad"}]},
    )
    assert invalid.status_code == 422
    assert fake_redis.stream == []


@pytest.mark.asyncio
async def test_ingest_requires_api_key(client):
    await register_and_create_service(client)
    response = await client.post("/api/v1/events/ingest", json={"events": [{"timestamp": "2026-11-12T10:00:00Z", "severity": "info", "source": "api", "message": "hello"}]})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_ingest_returns_service_unavailable_when_redis_fails(client):
    failing_redis = FailingRedis()
    from app.main import app

    app.dependency_overrides[get_redis] = lambda: failing_redis
    service = await register_and_create_service(client)
    response = await client.post(
        "/api/v1/events/ingest",
        headers={"X-API-Key": service["ingest_api_key"]},
        json={"events": [{"timestamp": "2026-11-12T10:00:00Z", "severity": "info", "source": "api", "message": "hello"}]},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_ingest_rejects_batches_over_configured_limit(client):
    fake_redis = FakeRedis()
    from app.main import app

    app.dependency_overrides[get_redis] = lambda: fake_redis
    service = await register_and_create_service(client)
    event = {"timestamp": "2026-11-12T10:00:00Z", "severity": "info", "source": "api", "message": "hello"}
    response = await client.post(
        "/api/v1/events/ingest",
        headers={"X-API-Key": service["ingest_api_key"]},
        json={"events": [event] * 101},
    )
    assert response.status_code == 413
    assert fake_redis.stream == []

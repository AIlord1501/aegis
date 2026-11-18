from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.main as main_module
from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.llm.providers import OpenAICompatibleModel, OpenAIEmbeddingModel
from app.llm.types import LLMError
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.user import User
from app.schemas.knowledge import SearchRequest
from app.services.search import search_chunks


async def _register_and_login(client, email: str) -> dict[str, str]:
    response = await client.post("/auth/register", json={"email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": "correct horse battery staple"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_openai_providers_raise_llm_error_on_empty_choices_and_data(monkeypatch):
    settings = Settings(llm_api_key="test-key", llm_max_retries=0)
    chat_model = OpenAICompatibleModel(settings)
    embed_model = OpenAIEmbeddingModel(settings)

    async def fake_post(self, url, **kwargs):
        payload = {"choices": []} if url.endswith("/chat/completions") else {"data": []}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(LLMError, match="no choices"):
        await chat_model.generate_json("Return {}")

    with pytest.raises(LLMError, match="no embedding data"):
        await embed_model.embed("hello")


@pytest.mark.asyncio
async def test_search_chunks_escapes_wildcards_and_matches_tokenized_terms():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as db:
            user = User(email="search@example.com", password_hash=hash_password("correct horse battery staple"))
            db.add(user)
            await db.flush()

            doc = KnowledgeDocument(
                owner_id=user.id,
                title="runbook.md",
                source_type="markdown",
                source_uri="upload://runbook.md",
                content_hash="abc123",
            )
            db.add(doc)
            await db.flush()

            exact_chunk = KnowledgeChunk(
                document_id=doc.id,
                chunk_index=0,
                content="100% cpu_usage spike in checkout",
                metadata_json={},
            )
            wildcard_decoy = KnowledgeChunk(
                document_id=doc.id,
                chunk_index=1,
                content="1000 cpuXusage normal in billing",
                metadata_json={},
            )
            db.add_all([exact_chunk, wildcard_decoy])
            await db.commit()

            wildcard_results = await search_chunks(
                db,
                owner_id=user.id,
                request=SearchRequest(query="100% cpu_usage"),
                llm=SimpleNamespace(),  # type: ignore[arg-type]
            )
            assert [result.chunk_id for result in wildcard_results] == [exact_chunk.id]

            tokenized_results = await search_chunks(
                db,
                owner_id=user.id,
                request=SearchRequest(query="checkout spike"),
                llm=SimpleNamespace(),  # type: ignore[arg-type]
            )
            assert [result.chunk_id for result in tokenized_results] == [exact_chunk.id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connect_repository_rejects_nonexistent_or_unowned_service(client):
    alice = await _register_and_login(client, "alice@example.com")
    bob = await _register_and_login(client, "bob@example.com")

    service_resp = await client.post("/services", headers=alice, json={"name": "payments"})
    assert service_resp.status_code == 201
    alice_service_id = service_resp.json()["id"]

    nonexistent_resp = await client.post(
        "/api/v1/repositories",
        headers=alice,
        json={"full_name": "acme/payments", "service_id": 99999},
    )
    assert nonexistent_resp.status_code == 404

    unowned_resp = await client.post(
        "/api/v1/repositories",
        headers=bob,
        json={"full_name": "acme/payments", "service_id": alice_service_id},
    )
    assert unowned_resp.status_code == 404


@pytest.mark.asyncio
async def test_events_ingest_rejects_malformed_content_length_and_oversized_chunked_body(client, monkeypatch):
    monkeypatch.setattr(main_module.settings, "event_max_request_bytes", 128)

    malformed_resp = await client.post(
        "/api/v1/events/ingest",
        content=b"{}",
        headers={"Content-Length": "not-an-integer", "Content-Type": "application/json"},
    )
    assert malformed_resp.status_code == 400

    async def oversized_chunks():
        yield b'{"events": ['
        yield b"x" * 200
        yield b"]}"

    chunked_resp = await client.post(
        "/api/v1/events/ingest",
        content=oversized_chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert chunked_resp.status_code == 413


@pytest.mark.asyncio
async def test_knowledge_file_upload_rejects_files_over_5mib(client):
    headers = await _register_and_login(client, "uploader@example.com")
    oversized_bytes = b"a" * (5 * 1024 * 1024 + 1)

    response = await client.post(
        "/api/v1/knowledge/documents/file",
        headers=headers,
        files={"file": ("oversized.txt", oversized_bytes, "text/plain")},
    )
    assert response.status_code == 413

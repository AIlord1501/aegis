from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.service import LLMService
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeJob, RepositoryConnection
from app.services.text import chunk_text, content_hash


async def ingest_text(
    db: AsyncSession,
    *,
    owner_id: int,
    title: str,
    source_uri: str,
    source_type: str,
    content: str,
    metadata: dict[str, Any],
    service_id: int | None = None,
    incident_id: int | None = None,
    repository_id: int | None = None,
    llm: LLMService | None = None,
) -> KnowledgeDocument:
    settings = get_settings()
    digest = content_hash(content)
    existing = await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.owner_id == owner_id, KnowledgeDocument.source_uri == source_uri, KnowledgeDocument.content_hash == digest))
    if existing:
        return existing
    previous_version = await db.scalar(select(func.max(KnowledgeDocument.version)).where(KnowledgeDocument.owner_id == owner_id, KnowledgeDocument.source_uri == source_uri))
    document = KnowledgeDocument(owner_id=owner_id, service_id=service_id, repository_id=repository_id, incident_id=incident_id, title=title, source_type=source_type, source_uri=source_uri, content_hash=digest, version=(previous_version or 0) + 1, metadata_json=metadata)
    db.add(document)
    await db.flush()
    model = llm or LLMService()
    for index, chunk in enumerate(chunk_text(content, settings.knowledge_chunk_size, settings.knowledge_chunk_overlap)):
        embedding = await model.embed(chunk)
        db.add(KnowledgeChunk(document_id=document.id, chunk_index=index, content=chunk, metadata_json={"title": title, "source_uri": source_uri}, embedding=embedding))
    await db.commit()
    await db.refresh(document)
    return document


async def create_job(db: AsyncSession, *, owner_id: int, job_type: str, source_uri: str | None) -> KnowledgeJob:
    job = KnowledgeJob(owner_id=owner_id, job_type=job_type, source_uri=source_uri, status="queued")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def mark_job(db: AsyncSession, job_id: int, *, status: str, error_message: str | None = None, documents_indexed: int = 0) -> None:
    job = await db.get(KnowledgeJob, job_id)
    if not job:
        return
    job.status = status
    job.error_message = error_message
    job.documents_indexed = documents_indexed
    job.completed_at = datetime.now(timezone.utc) if status in {"completed", "failed"} else None
    await db.commit()

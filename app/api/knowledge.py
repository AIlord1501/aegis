import asyncio
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, LLMDependency
from app.db.session import SessionLocal
from app.models.knowledge import KnowledgeJob
from app.schemas.knowledge import KnowledgeJobRead, KnowledgeTextCreate, SearchRequest, SearchResult
from app.services.knowledge import ingest_text, mark_job
from app.services.search import search_chunks
from app.services.text import parse_document

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@router.post("/documents/text", response_model=KnowledgeJobRead, status_code=status.HTTP_202_ACCEPTED)
async def ingest_text_document(payload: KnowledgeTextCreate, background_tasks: BackgroundTasks, current_user: CurrentUser, db: DbSession) -> KnowledgeJob:
    job = KnowledgeJob(owner_id=current_user.id, job_type="document_ingest", source_uri=payload.source_uri, status="queued")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(ingest_text_job, job.id, current_user.id, payload.model_dump())
    return job


@router.post("/documents/file", response_model=KnowledgeJobRead, status_code=status.HTTP_202_ACCEPTED)
async def ingest_file_document(background_tasks: BackgroundTasks, current_user: CurrentUser, db: DbSession, file: Annotated[UploadFile, File(...)]) -> KnowledgeJob:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A filename is required")
    max_bytes = 5 * 1024 * 1024
    chunks: list[bytes] = []
    total_bytes = 0
    while chunk := await file.read(65536):
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Knowledge file is too large")
        chunks.append(chunk)
    data = b"".join(chunks)
    content = await asyncio.to_thread(parse_document, data, file.filename)
    job = KnowledgeJob(owner_id=current_user.id, job_type="document_ingest", source_uri=f"upload://{file.filename}", status="queued")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(ingest_text_job, job.id, current_user.id, {"title": file.filename, "source_uri": f"upload://{file.filename}", "content": content, "source_type": file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "text", "metadata": {"filename": file.filename}})
    return job


async def ingest_text_job(job_id: int, owner_id: int, payload: dict) -> None:
    async with SessionLocal() as db:
        try:
            from app.llm.service import LLMService

            await ingest_text(db, owner_id=owner_id, title=payload["title"], source_uri=payload["source_uri"], source_type=payload.get("source_type", "text"), content=payload["content"], metadata=payload.get("metadata", {}), service_id=payload.get("service_id"), incident_id=payload.get("incident_id"), llm=LLMService())
            await mark_job(db, job_id, status="completed", documents_indexed=1)
        except Exception as exc:
            await db.rollback()
            await mark_job(db, job_id, status="failed", error_message=str(exc)[:1000])


@router.get("/jobs/{job_id}", response_model=KnowledgeJobRead)
async def get_knowledge_job(job_id: int, current_user: CurrentUser, db: DbSession) -> KnowledgeJob:
    job = await db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id, KnowledgeJob.owner_id == current_user.id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge job not found")
    return job


@router.post("/search", response_model=list[SearchResult])
async def search_knowledge(payload: SearchRequest, current_user: CurrentUser, db: DbSession, llm: LLMDependency) -> list[SearchResult]:
    return await search_chunks(db, owner_id=current_user.id, request=payload, llm=llm)

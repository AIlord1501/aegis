from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.secrets import encrypt_secret
from app.models.knowledge import KnowledgeJob, RepositoryConnection
from app.models.service import Service
from app.schemas.knowledge import KnowledgeJobRead
from app.schemas.repository import RepositoryCreate, RepositoryRead
from app.services.github import GitHubClient, GitHubError
from app.services.knowledge import ingest_text, mark_job
from app.db.session import SessionLocal
from app.llm.service import LLMService

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])


async def owned_repository(repository_id: int, owner_id: int, db: DbSession) -> RepositoryConnection:
    repository = await db.scalar(select(RepositoryConnection).where(RepositoryConnection.id == repository_id, RepositoryConnection.owner_id == owner_id))
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def repository_client(repository: RepositoryConnection) -> GitHubClient:
    from app.core.secrets import decrypt_secret

    return GitHubClient(decrypt_secret(repository.encrypted_token) if repository.encrypted_token else None)


@router.post("", response_model=RepositoryRead, status_code=status.HTTP_201_CREATED)
async def connect_repository(payload: RepositoryCreate, current_user: CurrentUser, db: DbSession) -> RepositoryConnection:
    if payload.service_id is not None:
        service = await db.scalar(select(Service).where(Service.id == payload.service_id, Service.owner_id == current_user.id))
        if service is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    existing = await db.scalar(select(RepositoryConnection).where(RepositoryConnection.owner_id == current_user.id, RepositoryConnection.full_name == payload.full_name))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Repository is already connected")
    try:
        metadata = await GitHubClient(payload.github_token).repository(payload.full_name)
    except GitHubError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to access GitHub repository") from exc
    try:
        encrypted_token = encrypt_secret(payload.github_token) if payload.github_token else None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    repository = RepositoryConnection(owner_id=current_user.id, service_id=payload.service_id, full_name=payload.full_name, default_branch=payload.default_branch, encrypted_token=encrypted_token, metadata_json={"html_url": metadata.get("html_url"), "description": metadata.get("description"), "private": metadata.get("private", False), "default_branch": metadata.get("default_branch", payload.default_branch)})
    db.add(repository)
    await db.commit()
    await db.refresh(repository)
    return repository


@router.get("", response_model=list[RepositoryRead])
async def list_repositories(current_user: CurrentUser, db: DbSession) -> list[RepositoryConnection]:
    result = await db.scalars(select(RepositoryConnection).where(RepositoryConnection.owner_id == current_user.id).order_by(RepositoryConnection.id))
    return list(result)


@router.post("/{repository_id}/index", response_model=KnowledgeJobRead, status_code=status.HTTP_202_ACCEPTED)
async def index_repository(repository_id: int, background_tasks: BackgroundTasks, current_user: CurrentUser, db: DbSession) -> KnowledgeJob:
    repository = await db.scalar(select(RepositoryConnection).where(RepositoryConnection.id == repository_id, RepositoryConnection.owner_id == current_user.id))
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    job = KnowledgeJob(owner_id=current_user.id, job_type="github_index", source_uri=repository.full_name, status="queued")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    background_tasks.add_task(index_repository_job, job.id, repository.id, current_user.id)
    return job


async def index_repository_job(job_id: int, repository_id: int, owner_id: int) -> None:
    async with SessionLocal() as db:
        try:
            repository = await db.scalar(select(RepositoryConnection).where(RepositoryConnection.id == repository_id, RepositoryConnection.owner_id == owner_id))
            if repository is None:
                await mark_job(db, job_id, status="failed", error_message="Repository not found")
                return
            from app.core.secrets import decrypt_secret

            token = decrypt_secret(repository.encrypted_token) if repository.encrypted_token else None
            client = GitHubClient(token)
            tree = await client.tree(repository.full_name, repository.default_branch)
            llm = LLMService()
            indexed = 0
            allowed = (".md", ".txt", ".py", ".go", ".ts", ".tsx", ".js", ".yaml", ".yml", ".json", ".sql")
            for item in tree:
                path = item.get("path", "")
                if not path.lower().endswith(allowed) and path.lower().split("/")[-1] not in {"readme", "dockerfile"}:
                    continue
                content, _ = await client.file(repository.full_name, path, repository.default_branch)
                await ingest_text(db, owner_id=owner_id, title=path, source_uri=f"github://{repository.full_name}/{path}", source_type="github", content=content, metadata={"repository": repository.full_name, "path": path, "branch": repository.default_branch}, repository_id=repository.id, llm=llm)
                indexed += 1
            repository.last_indexed_at = datetime.now(timezone.utc)
            await db.commit()
            await mark_job(db, job_id, status="completed", documents_indexed=indexed)
        except Exception as exc:
            await db.rollback()
            await mark_job(db, job_id, status="failed", error_message=str(exc)[:1000])


@router.get("/jobs/{job_id}", response_model=KnowledgeJobRead)
async def get_repository_job(job_id: int, current_user: CurrentUser, db: DbSession) -> KnowledgeJob:
    job = await db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id, KnowledgeJob.owner_id == current_user.id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge job not found")
    return job


@router.get("/{repository_id}/commits")
async def repository_commits(repository_id: int, current_user: CurrentUser, db: DbSession, limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    repository = await owned_repository(repository_id, current_user.id, db)
    try:
        return await repository_client(repository).commits(repository.full_name, repository.default_branch, limit)
    except GitHubError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to retrieve repository commits") from exc


@router.get("/{repository_id}/pull-requests")
async def repository_pull_requests(repository_id: int, current_user: CurrentUser, db: DbSession, limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    repository = await owned_repository(repository_id, current_user.id, db)
    try:
        return await repository_client(repository).pull_requests(repository.full_name, limit)
    except GitHubError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to retrieve pull requests") from exc


@router.get("/{repository_id}/files")
async def repository_file(repository_id: int, path: str, current_user: CurrentUser, db: DbSession) -> dict[str, str]:
    repository = await owned_repository(repository_id, current_user.id, db)
    try:
        content, metadata = await repository_client(repository).file(repository.full_name, path, repository.default_branch)
    except GitHubError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to retrieve repository file") from exc
    return {"path": path, "content": content, "html_url": metadata.get("html_url", "")}


@router.get("/{repository_id}/code-search")
async def repository_code_search(repository_id: int, query: str, current_user: CurrentUser, db: DbSession) -> list[dict]:
    repository = await owned_repository(repository_id, current_user.id, db)
    try:
        return await repository_client(repository).code_search(repository.full_name, query)
    except GitHubError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to search repository code") from exc

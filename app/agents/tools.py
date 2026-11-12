import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import decrypt_secret
from app.llm.service import LLMService
from app.llm.types import ModelResult
from app.models.event import Event
from app.models.incident import Incident
from app.models.investigation import ToolExecution
from app.models.knowledge import RepositoryConnection
from app.models.service import Service
from app.services.github import GitHubClient, GitHubError
from app.services.search import search_chunks
from app.schemas.knowledge import SearchRequest


class SearchLogsInput(BaseModel):
    service_id: int
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=50, ge=1, le=200)


class IncidentDetailsInput(BaseModel):
    incident_id: int


class SimilarIncidentsInput(BaseModel):
    incident_id: int
    limit: int = Field(default=10, ge=1, le=50)


class KnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    service_id: int | None = None
    repository_id: int | None = None
    document_id: int | None = None
    incident_id: int | None = None
    top_k: int = Field(default=8, ge=1, le=30)


class RepositorySearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    repository_id: int | None = None
    service_id: int | None = None


class InspectFileInput(BaseModel):
    repository_id: int
    path: str = Field(min_length=1, max_length=1000)


class InspectCommitInput(BaseModel):
    repository_id: int
    sha: str = Field(min_length=7, max_length=64)


class RemediationPlanInput(BaseModel):
    findings: list[dict[str, Any]] = Field(default_factory=list, max_length=30)


class PermittedTestsInput(BaseModel):
    repository_id: int | None = None
    test_name: str = Field(pattern=r"^(unit|lint|compile|readiness)$")


T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class ToolContext:
    db: AsyncSession
    owner_id: int
    investigation_id: int
    agent_execution_id: int | None
    llm: LLMService
    timeout_seconds: float = 30.0
    last_model_result: ModelResult | None = None

    async def ask_model(self, prompt: str) -> dict[str, Any]:
        result = await self.llm.generate_json(prompt)
        self.last_model_result = result
        return result.output

    async def execute(self, name: str, payload: BaseModel, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        started = time.perf_counter()
        record = ToolExecution(investigation_id=self.investigation_id, agent_execution_id=self.agent_execution_id, tool_name=name, status="running", input_json=payload.model_dump(mode="json"), output_json={})
        self.db.add(record)
        await self.db.flush()
        try:
            output = await operation()
            record.status = "completed"
            record.output_json = output
            return output
        except Exception as exc:
            record.status = "failed"
            record.error_message = str(exc)[:1000]
            raise
        finally:
            record.completed_at = datetime.now(timezone.utc)
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            await self.db.commit()


async def search_logs(ctx: ToolContext, payload: SearchLogsInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        owned = await ctx.db.scalar(select(Service.id).where(Service.id == payload.service_id, Service.owner_id == ctx.owner_id))
        if owned is None:
            raise PermissionError("Service is not owned by the investigation user")
        query = select(Event).where(Event.service_id == payload.service_id).order_by(Event.occurred_at.desc()).limit(payload.limit)
        events = list((await ctx.db.scalars(query)).all())
        if payload.query:
            needle = payload.query.lower()
            events = [event for event in events if needle in event.message.lower() or needle in event.source.lower()]
        return {"events": [{"id": event.id, "timestamp": event.occurred_at.isoformat(), "severity": event.severity, "source": event.source, "message": event.message, "attributes": event.attributes} for event in events]}

    return await ctx.execute("search_logs", payload, operation)


async def get_incident_details(ctx: ToolContext, payload: IncidentDetailsInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        incident = await ctx.db.scalar(select(Incident).join(Service).where(Incident.id == payload.incident_id, Service.owner_id == ctx.owner_id))
        if incident is None:
            raise LookupError("Incident not found")
        return {"id": incident.id, "service_id": incident.service_id, "title": incident.title, "description": incident.description, "status": incident.status, "created_at": incident.created_at.isoformat() if incident.created_at else None}

    return await ctx.execute("get_incident_details", payload, operation)


async def search_similar_incidents(ctx: ToolContext, payload: SimilarIncidentsInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        incident = await ctx.db.scalar(select(Incident).join(Service).where(Incident.id == payload.incident_id, Service.owner_id == ctx.owner_id))
        if incident is None:
            raise LookupError("Incident not found")
        candidates = list((await ctx.db.scalars(select(Incident).where(Incident.service_id == incident.service_id, Incident.id != incident.id).order_by(Incident.created_at.desc()).limit(payload.limit))).all())
        return {"incidents": [{"id": item.id, "title": item.title, "description": item.description, "status": item.status} for item in candidates]}

    return await ctx.execute("search_similar_incidents", payload, operation)


async def search_knowledge(ctx: ToolContext, payload: KnowledgeSearchInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        results = await search_chunks(ctx.db, owner_id=ctx.owner_id, request=SearchRequest(**payload.model_dump()), llm=ctx.llm)
        return {"results": [result.model_dump() for result in results]}

    return await ctx.execute("search_knowledge", payload, operation)


async def _owned_repository(ctx: ToolContext, repository_id: int) -> RepositoryConnection:
    repository = await ctx.db.scalar(select(RepositoryConnection).where(RepositoryConnection.id == repository_id, RepositoryConnection.owner_id == ctx.owner_id))
    if repository is None:
        raise PermissionError("Repository is not owned by the investigation user")
    return repository


def _github_client(repository: RepositoryConnection) -> GitHubClient:
    return GitHubClient(decrypt_secret(repository.encrypted_token) if repository.encrypted_token else None)


async def search_repository(ctx: ToolContext, payload: RepositorySearchInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        if payload.repository_id is not None:
            repositories = [await _owned_repository(ctx, payload.repository_id)]
        else:
            query = select(RepositoryConnection).where(RepositoryConnection.owner_id == ctx.owner_id)
            if payload.service_id is not None:
                query = query.where(RepositoryConnection.service_id == payload.service_id)
            repositories = list((await ctx.db.scalars(query)).all())
        results = []
        for repository in repositories:
            results.extend(await _github_client(repository).code_search(repository.full_name, payload.query))
        return {"results": results}

    return await ctx.execute("search_repository", payload, operation)


async def inspect_file(ctx: ToolContext, payload: InspectFileInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        repository = await _owned_repository(ctx, payload.repository_id)
        content, metadata = await _github_client(repository).file(repository.full_name, payload.path, repository.default_branch)
        return {"repository": repository.full_name, "path": payload.path, "content": content, "url": metadata.get("html_url")}

    return await ctx.execute("inspect_file", payload, operation)


async def inspect_commit(ctx: ToolContext, payload: InspectCommitInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        repository = await _owned_repository(ctx, payload.repository_id)
        commits = await _github_client(repository).commits(repository.full_name, payload.sha, 100)
        match = next((commit for commit in commits if commit.get("sha", "").startswith(payload.sha)), None)
        if match is None:
            return {"repository": repository.full_name, "sha": payload.sha, "found": False}
        return {"repository": repository.full_name, "sha": match.get("sha"), "message": match.get("commit", {}).get("message"), "author": match.get("commit", {}).get("author"), "url": match.get("html_url")}

    return await ctx.execute("inspect_commit", payload, operation)


async def generate_remediation_plan(ctx: ToolContext, payload: RemediationPlanInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        return {"recommendations": [{"action": "validate", "description": "Validate this finding against production-safe evidence before changing systems.", "finding": finding} for finding in payload.findings], "requires_human_approval": True}

    return await ctx.execute("generate_remediation_plan", payload, operation)


async def run_permitted_tests(ctx: ToolContext, payload: PermittedTestsInput) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        return {"test_name": payload.test_name, "status": "not_run", "reason": "Phase 4 permits only an isolated executor; no arbitrary shell execution is enabled.", "repository_id": payload.repository_id}

    return await ctx.execute("run_permitted_tests", payload, operation)


TOOL_FUNCTIONS = {
    "search_logs": search_logs,
    "get_incident_details": get_incident_details,
    "search_similar_incidents": search_similar_incidents,
    "search_knowledge": search_knowledge,
    "search_repository": search_repository,
    "inspect_file": inspect_file,
    "inspect_commit": inspect_commit,
    "generate_remediation_plan": generate_remediation_plan,
    "run_permitted_tests": run_permitted_tests,
}

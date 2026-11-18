import json
import time
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService
from app.llm.types import LLMError
from app.models.event import Event
from app.models.incident import Incident
from app.models.intelligence import InvestigationReport, ModelCall
from app.models.knowledge import RepositoryConnection
from app.models.service import Service
from app.schemas.knowledge import SearchRequest
from app.services.search import search_chunks
from app.services.github import GitHubClient, GitHubError


async def analyze_incident(db: AsyncSession, *, owner_id: int, incident_id: int, llm: LLMService) -> InvestigationReport:
    incident = await db.scalar(select(Incident).join(Service).where(Incident.id == incident_id, Service.owner_id == owner_id))
    if incident is None:
        raise LookupError("Incident not found")
    service = await db.get(Service, incident.service_id)
    events = list((await db.scalars(select(Event).where(Event.service_id == incident.service_id).order_by(Event.occurred_at.desc()).limit(100))).all())
    similar = list((await db.scalars(select(Incident).where(Incident.service_id == incident.service_id, Incident.id != incident.id).order_by(Incident.created_at.desc()).limit(5))).all())
    related = await search_chunks(db, owner_id=owner_id, request=SearchRequest(query=f"{incident.title} {incident.description or ''}", service_id=incident.service_id, top_k=8), llm=llm)
    repository_context: list[dict[str, object]] = []
    repositories = list((await db.scalars(select(RepositoryConnection).where(RepositoryConnection.owner_id == owner_id, RepositoryConnection.service_id == incident.service_id))).all())
    for repository in repositories:
        try:
            from app.core.secrets import decrypt_secret

            token = decrypt_secret(repository.encrypted_token) if repository.encrypted_token else None
            commits = await GitHubClient(token).commits(repository.full_name, repository.default_branch, 10)
            repository_context.append({"repository": repository.full_name, "commits": [{"sha": item.get("sha"), "message": item.get("commit", {}).get("message"), "url": item.get("html_url")} for item in commits]})
        except (GitHubError, ValueError) as exc:
            repository_context.append({"repository": repository.full_name, "error": str(exc)})
    verified_evidence = [
        {"type": "incident", "source": f"incident:{incident.id}", "summary": incident.description or incident.title},
        {"type": "service", "source": f"service:{service.id}", "summary": service.name},
        *[{"type": "log_event", "source": f"event:{event.id}", "timestamp": event.occurred_at.isoformat(), "severity": event.severity, "summary": event.message} for event in events[:20]],
        *[{"type": "knowledge", "source": result.citation, "summary": result.content, "uri": result.source_uri} for result in related],
        *[{"type": "repository_commits", "source": item["repository"], "summary": item} for item in repository_context],
    ]
    prompt = json.dumps({
        "task": "Analyze the engineering incident using only the verified evidence. Return JSON with summary, severity_assessment, potential_root_causes, relevant_code_references, recommended_investigation_steps, suggested_remediation, and confidence. Mark unverified reasoning as hypotheses and never present hypotheses as facts.",
        "incident": {"id": incident.id, "title": incident.title, "description": incident.description, "status": incident.status, "service": service.name},
        "event_frequency": dict(Counter(event.severity for event in events)),
        "similar_historical_incidents": [{"id": item.id, "title": item.title, "description": item.description} for item in similar],
        "recent_repository_commits": repository_context,
        "verified_evidence": verified_evidence,
    }, ensure_ascii=False)
    started = time.perf_counter()
    try:
        result = await llm.generate_json(prompt)
        model_call = ModelCall(owner_id=owner_id, incident_id=incident.id, provider=result.provider, model_name=result.model, status=result.status, latency_ms=result.latency_ms, prompt_tokens=result.usage.prompt_tokens, completion_tokens=result.usage.completion_tokens)
        report_payload = {
            "incident_summary": result.output.get("summary", incident.title),
            "severity_assessment": result.output.get("severity_assessment", "unknown"),
            "verified_evidence": verified_evidence,
            "hypotheses": result.output.get("potential_root_causes", []),
            "relevant_code_references": result.output.get("relevant_code_references", []),
            "recommended_investigation_steps": result.output.get("recommended_investigation_steps", []),
            "suggested_remediation": result.output.get("suggested_remediation", []),
            "confidence": result.output.get("confidence"),
            "citations": [item.citation for item in related],
            "model_output": result.output,
        }
    except LLMError as exc:
        model_call = ModelCall(owner_id=owner_id, incident_id=incident.id, provider=exc.provider, model_name=exc.model, status="error", latency_ms=int((time.perf_counter() - started) * 1000), error_message=str(exc))
        db.add(model_call)
        await db.commit()
        raise
    db.add(model_call)
    await db.flush()
    report = InvestigationReport(owner_id=owner_id, incident_id=incident.id, model_call_id=model_call.id, status="completed", report_json=report_payload)
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report

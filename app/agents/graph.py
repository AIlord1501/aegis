import asyncio
import operator
import time
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, update

from app.agents.tools import (
    IncidentDetailsInput,
    KnowledgeSearchInput,
    PermittedTestsInput,
    RemediationPlanInput,
    RepositorySearchInput,
    SearchLogsInput,
    SimilarIncidentsInput,
    ToolContext,
    generate_remediation_plan,
    get_incident_details,
    run_permitted_tests,
    search_knowledge,
    search_logs,
    search_repository,
    search_similar_incidents,
)
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.llm.service import LLMService
from app.llm.types import LLMError
from app.models.intelligence import InvestigationReport
from app.models.investigation import AgentExecution, Approval, Investigation, InvestigationCheckpoint


class InvestigationState(TypedDict, total=False):
    investigation_id: int
    owner_id: int
    incident_id: int
    priority: str
    plan: dict[str, Any]
    findings: Annotated[list[dict[str, Any]], operator.add]
    agent_outputs: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    resolution: dict[str, Any]
    verification: dict[str, Any]
    requires_approval: bool
    status: str


async def _model(ctx: ToolContext, task: str, evidence: dict[str, Any]) -> dict[str, Any]:
    try:
        return await ctx.ask_model(f"""You are a bounded Aegis investigation agent. {task}
Use only the evidence below. Separate verified facts from hypotheses. Return a JSON object.
Evidence:
{evidence}
""")
    except LLMError as exc:
        return {"status": "model_unavailable", "model_error": str(exc), "verified_facts": evidence}


async def triage_agent(ctx: ToolContext, state: InvestigationState) -> dict[str, Any]:
    incident = await get_incident_details(ctx, IncidentDetailsInput(incident_id=state["incident_id"]))
    logs = await search_logs(ctx, SearchLogsInput(service_id=incident["service_id"], limit=30))
    output = await _model(ctx, "Triage the incident, set priority to low, medium, high, or critical, and produce an investigation plan.", {"incident": incident, "initial_logs": logs})
    priority = output.get("priority", "high" if logs.get("events") else "medium")
    return {"priority": priority, "plan": output, "agent_outputs": [{"agent": "triage", "output": output}]}


async def investigation_agent(ctx: ToolContext, state: InvestigationState) -> dict[str, Any]:
    incident = await get_incident_details(ctx, IncidentDetailsInput(incident_id=state["incident_id"]))
    logs = await search_logs(ctx, SearchLogsInput(service_id=incident["service_id"], query=incident.get("title"), limit=100))
    similar = await search_similar_incidents(ctx, SimilarIncidentsInput(incident_id=state["incident_id"]))
    output = await _model(ctx, "Investigate logs, recurrence, severity frequency, and timelines. Identify suspicious patterns but label them as hypotheses.", {"incident": incident, "logs": logs, "similar_incidents": similar})
    return {"findings": [{"agent": "investigation", "type": "logs", "output": output}], "agent_outputs": [{"agent": "investigation", "output": output}]}


async def code_agent(ctx: ToolContext, state: InvestigationState) -> dict[str, Any]:
    incident = await get_incident_details(ctx, IncidentDetailsInput(incident_id=state["incident_id"]))
    search = await search_repository(ctx, RepositorySearchInput(query=incident["title"], service_id=incident["service_id"]))
    output = await _model(ctx, "Analyze repository search results and identify relevant files, functions, and potentially problematic changes. Include references when present.", {"incident": incident, "repository_results": search})
    return {"findings": [{"agent": "code_analysis", "type": "code", "output": output}], "agent_outputs": [{"agent": "code_analysis", "output": output}]}


async def knowledge_agent(ctx: ToolContext, state: InvestigationState) -> dict[str, Any]:
    incident = await get_incident_details(ctx, IncidentDetailsInput(incident_id=state["incident_id"]))
    search = await search_knowledge(ctx, KnowledgeSearchInput(query=f"{incident['title']} {incident.get('description') or ''}", service_id=incident["service_id"], top_k=8))
    output = await _model(ctx, "Retrieve grounded documentation and historical context. Preserve citations and distinguish facts from hypotheses.", {"incident": incident, "knowledge_results": search})
    return {"findings": [{"agent": "knowledge", "type": "knowledge", "output": output}], "agent_outputs": [{"agent": "knowledge", "output": output}]}


async def resolution_agent(ctx: ToolContext, state: InvestigationState) -> dict[str, Any]:
    plan = await generate_remediation_plan(ctx, RemediationPlanInput(findings=state.get("findings", [])))
    output = await _model(ctx, "Combine the findings into a probable root cause, confidence assessment, and remediation plan. Any proposed action requires human approval.", {"findings": state.get("findings", []), "remediation_tool": plan})
    resolution = {"analysis": output, "remediation": plan, "requires_approval": bool(plan.get("recommendations"))}
    return {"resolution": resolution, "requires_approval": resolution["requires_approval"], "agent_outputs": [{"agent": "resolution", "output": resolution}]}


async def verification_agent(ctx: ToolContext, state: InvestigationState) -> dict[str, Any]:
    tests = await run_permitted_tests(ctx, PermittedTestsInput(test_name="readiness"))
    output = await _model(ctx, "Verify whether the evidence supports the resolution. Identify unresolved risks and tests that still need human-approved isolated execution.", {"resolution": state.get("resolution", {}), "permitted_checks": tests})
    return {"verification": {"analysis": output, "checks": tests}, "agent_outputs": [{"agent": "verification", "output": output}]}


async def _run_agent_node(node_name: str, function, state: InvestigationState, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    settings = get_settings()
    async with semaphore:
        async with SessionLocal() as db:
            execution = AgentExecution(investigation_id=state["investigation_id"], agent_name=node_name, status="running", input_json={"incident_id": state["incident_id"]}, output_json={})
            db.add(execution)
            await db.flush()
            ctx = ToolContext(db=db, owner_id=state["owner_id"], investigation_id=state["investigation_id"], agent_execution_id=execution.id, llm=LLMService(), timeout_seconds=settings.agent_timeout_seconds)
            started = time.perf_counter()
            result: dict[str, Any] = {}
            for attempt in range(settings.agent_max_retries + 1):
                try:
                    result = await asyncio.wait_for(function(ctx, state), timeout=settings.agent_timeout_seconds)
                    execution.status = "completed"
                    execution.output_json = result
                    execution.retry_count = attempt
                    if ctx.last_model_result:
                        execution.model_name = ctx.last_model_result.model
                        execution.prompt_tokens = ctx.last_model_result.usage.prompt_tokens
                        execution.completion_tokens = ctx.last_model_result.usage.completion_tokens
                    break
                except (LookupError, PermissionError) as exc:
                    execution.retry_count = attempt
                    execution.status = "failed"
                    execution.error_message = str(exc)[:1000]
                    result = {"agent_outputs": [{"agent": node_name, "status": "failed", "error": str(exc)}], "errors": [{"agent": node_name, "error": str(exc)}]}
                    break
                except Exception as exc:
                    execution.retry_count = attempt + 1
                    if attempt == settings.agent_max_retries:
                        execution.status = "failed"
                        execution.error_message = str(exc)[:1000]
                        result = {"agent_outputs": [{"agent": node_name, "status": "failed", "error": str(exc)}], "errors": [{"agent": node_name, "error": str(exc)}]}
            execution.completed_at = datetime.now(timezone.utc)
            execution.duration_ms = int((time.perf_counter() - started) * 1000)
            merged_state = dict(state)
            for key, value in result.items():
                if key in {"findings", "agent_outputs", "errors"}:
                    merged_state[key] = list(state.get(key, [])) + list(value)
                else:
                    merged_state[key] = value
            checkpoint = InvestigationCheckpoint(investigation_id=state["investigation_id"], node_name=node_name, state_json=merged_state)
            await db.execute(
                update(Investigation)
                .where(Investigation.id == state["investigation_id"])
                .values(iteration_count=Investigation.iteration_count + 1)
            )
            db.add(checkpoint)
            await db.commit()
            return result


async def _aggregate_node(state: InvestigationState) -> dict[str, Any]:
    async with SessionLocal() as db:
        investigation = await db.get(Investigation, state["investigation_id"])
        if investigation:
            investigation.state_json = dict(state)
        db.add(InvestigationCheckpoint(investigation_id=state["investigation_id"], node_name="aggregate", state_json=dict(state)))
        await db.commit()
    return {"status": "aggregated"}


async def _request_approval_node(state: InvestigationState) -> dict[str, Any]:
    async with SessionLocal() as db:
        existing = await db.scalar(select(Approval).where(Approval.investigation_id == state["investigation_id"], Approval.status == "pending"))
        if existing is None:
            db.add(Approval(investigation_id=state["investigation_id"], owner_id=state["owner_id"], action_type="remediation", proposal_json=state.get("resolution", {})))
        investigation = await db.get(Investigation, state["investigation_id"])
        if investigation:
            investigation.status = "waiting_approval"
            investigation.state_json = dict(state)
        db.add(InvestigationCheckpoint(investigation_id=state["investigation_id"], node_name="request_approval", state_json=dict(state)))
        await db.commit()
    return {"status": "waiting_approval"}


async def _finalize_node(state: InvestigationState) -> dict[str, Any]:
    async with SessionLocal() as db:
        investigation = await db.get(Investigation, state["investigation_id"])
        if investigation:
            investigation.status = "completed"
            investigation.state_json = dict(state)
            investigation.completed_at = datetime.now(timezone.utc)
            investigation.iteration_count += 1
            db.add(InvestigationReport(owner_id=investigation.owner_id, investigation_id=investigation.id, incident_id=investigation.incident_id, status="completed", report_json={
                    "incident_overview": {"incident_id": investigation.incident_id},
                    "severity": state.get("priority"),
                    "investigation_timeline": state.get("agent_outputs", []),
                    "agents_involved": [item.get("agent") for item in state.get("agent_outputs", [])],
                    "evidence_collected": state.get("findings", []),
                    "probable_root_cause": state.get("resolution", {}).get("analysis", {}).get("summary"),
                    "confidence_assessment": state.get("resolution", {}).get("analysis", {}).get("confidence"),
                    "recommended_remediation": state.get("resolution", {}).get("remediation", {}),
                    "verification_results": state.get("verification", {}),
                    "outstanding_risks": state.get("verification", {}).get("analysis", {}).get("unresolved_risks", []),
                    "facts": state.get("findings", []),
                    "hypotheses": state.get("resolution", {}).get("analysis", {}).get("potential_root_causes", []),
            }))
            await db.commit()
    return {"status": "completed"}


def build_graph(*, start_at: str = "triage"):
    graph = StateGraph(InvestigationState)
    semaphore = asyncio.Semaphore(get_settings().agent_max_concurrency)
    graph.add_node("triage", lambda state: _run_agent_node("triage", triage_agent, state, semaphore))
    graph.add_node("investigation", lambda state: _run_agent_node("investigation", investigation_agent, state, semaphore))
    graph.add_node("code_analysis", lambda state: _run_agent_node("code_analysis", code_agent, state, semaphore))
    graph.add_node("knowledge", lambda state: _run_agent_node("knowledge", knowledge_agent, state, semaphore))
    graph.add_node("aggregate", _aggregate_node)
    graph.add_node("resolution", lambda state: _run_agent_node("resolution", resolution_agent, state, semaphore))
    graph.add_node("verification", lambda state: _run_agent_node("verification", verification_agent, state, semaphore))
    graph.add_node("request_approval", _request_approval_node)
    graph.add_node("finalize", _finalize_node)
    graph.add_edge(START, start_at)
    if start_at == "triage":
        graph.add_edge("triage", "investigation")
        graph.add_edge("triage", "code_analysis")
        graph.add_edge("triage", "knowledge")
        graph.add_edge("investigation", "aggregate")
        graph.add_edge("code_analysis", "aggregate")
        graph.add_edge("knowledge", "aggregate")
        graph.add_edge("aggregate", "resolution")
        graph.add_edge("resolution", "verification")
    graph.add_conditional_edges("verification", lambda state: "request_approval" if state.get("requires_approval") else "finalize", {"request_approval": "request_approval", "finalize": "finalize"})
    graph.add_edge("request_approval", END)
    graph.add_edge("finalize", END)
    return graph.compile()


async def run_investigation(investigation_id: int, *, resume_from: str | None = None) -> None:
    async with SessionLocal() as db:
        investigation = await db.get(Investigation, investigation_id)
        if investigation is None:
            return
        state: InvestigationState = dict(investigation.state_json or {})
        state.update({"investigation_id": investigation.id, "owner_id": investigation.owner_id, "incident_id": investigation.incident_id, "status": "running"})
        if resume_from:
            state["requires_approval"] = False
        investigation.status = "running"
        investigation.started_at = investigation.started_at or datetime.now(timezone.utc)
        await db.commit()
    try:
        graph = build_graph(start_at=resume_from or "triage")
        result = await graph.ainvoke(state)
        async with SessionLocal() as db:
            current = await db.get(Investigation, investigation_id)
            if current and current.status == "running":
                current.state_json = dict(result)
                if result.get("status") == "completed":
                    current.status = "completed"
                await db.commit()
    except Exception as exc:
        async with SessionLocal() as db:
            current = await db.get(Investigation, investigation_id)
            if current:
                current.status = "failed"
                current.error_message = str(exc)[:1000]
                current.completed_at = datetime.now(timezone.utc)
                await db.commit()

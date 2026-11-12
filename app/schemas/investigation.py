from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvestigationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    thread_id: str
    status: str
    priority: str | None
    error_message: str | None
    iteration_count: int
    max_iterations: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class AgentExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    investigation_id: int
    agent_name: str
    status: str
    input_json: dict[str, Any]
    output_json: dict[str, Any]
    model_name: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    retry_count: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    investigation_id: int
    status: str
    action_type: str
    proposal_json: dict[str, Any]
    reviewed_by: int | None
    review_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None


class ApprovalDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)

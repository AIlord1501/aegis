from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeTextCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    source_uri: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1)
    source_type: str = Field(default="text", min_length=1, max_length=40)
    service_id: int | None = None
    incident_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_type: str
    status: str
    source_uri: str | None
    error_message: str | None
    documents_indexed: int
    created_at: datetime
    completed_at: datetime | None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    service_id: int | None = None
    repository_id: int | None = None
    document_id: int | None = None
    incident_id: int | None = None
    top_k: int = Field(default=8, ge=1, le=50)


class SearchResult(BaseModel):
    chunk_id: int
    document_id: int
    title: str
    source_type: str
    source_uri: str
    chunk_index: int
    content: str
    score: float
    citation: str

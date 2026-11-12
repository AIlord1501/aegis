from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RepositoryCreate(BaseModel):
    full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    default_branch: str = Field(default="main", min_length=1, max_length=200)
    service_id: int | None = None
    github_token: str | None = None


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    default_branch: str
    metadata_json: dict[str, Any]
    connected_at: datetime
    last_indexed_at: datetime | None

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class ServiceRead(ServiceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    ingest_api_key_prefix: str | None


class ServiceCreated(ServiceRead):
    ingest_api_key: str

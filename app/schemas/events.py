from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class StructuredLogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(default=None, max_length=200)
    timestamp: datetime
    severity: Severity
    source: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=20_000)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class EventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[StructuredLogEvent] = Field(min_length=1)

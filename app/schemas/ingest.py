from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    accepted: int = Field(ge=0)
    stream: str

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class InvestigationReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    status: str
    report_json: dict[str, Any]
    created_at: datetime

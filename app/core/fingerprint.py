import hashlib
import json
from datetime import datetime
from typing import Any


def event_fingerprint(
    *,
    service_id: int,
    event_id: str | None,
    occurred_at: datetime,
    severity: str,
    source: str,
    message: str,
    attributes: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "attributes": attributes,
            "event_id": event_id,
            "message": message,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "service_id": service_id,
            "severity": severity,
            "source": source,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

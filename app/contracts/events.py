import uuid
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.time import utc_now


class EventEnvelope(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    event_version: int = Field(gt=0)
    aggregate_type: str
    aggregate_id: uuid.UUID
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    causation_id: uuid.UUID | None = None
    occurred_at: AwareDatetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid")


class AuditEnvelope(BaseModel):
    actor_type: str
    actor_id: uuid.UUID | None = None
    action: str
    target_type: str
    target_id: uuid.UUID | None = None
    reason_code: str
    correlation_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: AwareDatetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid")

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMRunSnapshotCreate(BaseModel):
    run_type: str
    provider: str | None = None
    model_name: str | None = None
    prompt_template_key: str | None = None
    prompt_template_version: str | None = None
    input_payload: dict[str, Any]
    input_hash: str
    output_payload: dict[str, Any] | None = None
    output_hash: str | None = None
    status: str
    cost_payload: dict[str, Any] | None = None
    correlation_id: str

    model_config = ConfigDict(extra="forbid")

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ProviderStackDriftGuardRead(BaseModel):
    generated_at: AwareDatetime
    status: Literal["PASS", "PROVIDER_STACK_DRIFT"]
    expected_provider_keys: list[str] = Field(default_factory=list)
    found_active_provider_keys: list[str] = Field(default_factory=list)
    stale_provider_keys: list[str] = Field(default_factory=list)
    affected_catalogs: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str
    no_provider_call_made: bool = True

    model_config = ConfigDict(extra="forbid")

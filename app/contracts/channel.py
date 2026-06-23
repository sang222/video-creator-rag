import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ChannelStatus = Literal["draft", "active", "paused", "archived"]
MembershipStatus = Literal["active", "paused", "removed"]


class ChannelWorkspaceCreate(BaseModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: ChannelStatus = "draft"
    primary_language: str = "en"
    target_market: str | None = None
    default_timezone: str = "UTC"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ChannelWorkspaceRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    key: str
    name: str
    status: ChannelStatus
    primary_language: str
    target_market: str | None
    default_timezone: str
    active_policy_snapshot_id: uuid.UUID | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")


class ChannelMembershipCreate(BaseModel):
    user_id: uuid.UUID
    role_id: uuid.UUID
    status: MembershipStatus = "active"

    model_config = ConfigDict(extra="forbid")


class ChannelMembershipRead(BaseModel):
    id: uuid.UUID
    channel_workspace_id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    status: MembershipStatus
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid")

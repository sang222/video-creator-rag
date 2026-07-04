import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


EmbeddingJobStatus = Literal["PENDING", "READY", "EMBEDDED", "STALE", "FAILED", "BLOCKED"]
EmbeddingStaleState = Literal["FRESH", "STALE", "REINDEX_REQUIRED", "BLOCKED"]
RetrievalStatus = Literal["OK", "EMPTY_SAFE_DIGEST", "VECTOR_RUNTIME_EMPTY_SAFE", "OK_DETERMINISTIC_NO_VECTOR", "BLOCKED"]


class RetrievalPolicy(BaseModel):
    allow_company_approved: bool = False
    max_selected_facets: int = 5
    max_digest_chars: int = 1200
    requested_facet_types: list[str] = Field(default_factory=list)
    vector_enabled: bool = False
    ranking_params_json: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RetrievalRequest(BaseModel):
    effective_context_snapshot_id: uuid.UUID
    agent_key: str
    use_case: str
    company_id: uuid.UUID | None = None
    channel_workspace_id: uuid.UUID | None = None
    content_category_id: uuid.UUID | None = None
    series_id: uuid.UUID | None = None
    character_profile_id: uuid.UUID | None = None
    character_version_id: uuid.UUID | None = None
    video_project_id: uuid.UUID | None = None
    package_id: uuid.UUID | None = None
    query_facet_type: str | None = None
    query_text: str = ""
    query_vector: list[float] | None = None
    policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)

    model_config = ConfigDict(extra="forbid")


class RetrievalCandidate(BaseModel):
    memory_item_id: uuid.UUID
    memory_facet_id: uuid.UUID
    facet_type: str
    facet_text_hash: str
    deterministic_score: float
    vector_score: float | None = None
    final_score: float
    polarity: str
    confidence_label: str
    scope: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RetrievalResult(BaseModel):
    status: RetrievalStatus
    manifest_id: uuid.UUID
    retrieval_hash: str
    digest: dict[str, Any]
    selected_candidates: list[RetrievalCandidate] = Field(default_factory=list)
    blocked_refs: list[dict[str, Any]] = Field(default_factory=list)
    rejected_refs: list[dict[str, Any]] = Field(default_factory=list)
    sql_filter_applied_before_vector: bool = True
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class EmbeddingFacetRead(BaseModel):
    id: uuid.UUID
    memory_facet_id: uuid.UUID
    memory_item_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    content_category_id: uuid.UUID | None
    series_id: uuid.UUID | None
    character_profile_id: uuid.UUID | None
    character_version_id: uuid.UUID | None
    facet_type: str
    facet_text_hash: str
    embedding_model: str
    embedding_dimension: int
    embedding_vector_json: list[float]
    approval_status_at_embed: str
    rights_status_at_embed: str
    prompt_safety_state_at_embed: str
    embedding_eligible_at_embed: bool
    stale_state: EmbeddingStaleState
    stale_reason_codes_json: list[str]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class EmbeddingJobRead(BaseModel):
    id: uuid.UUID
    memory_facet_id: uuid.UUID
    job_status: EmbeddingJobStatus
    blocker_reason_codes_json: list[str]
    embedding_model: str | None
    embedding_dimension: int | None
    attempt_count: int
    last_error: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class VectorRetrievalManifestRead(BaseModel):
    id: uuid.UUID
    video_project_id: uuid.UUID | None
    package_id: uuid.UUID | None
    effective_context_snapshot_id: uuid.UUID
    company_id: uuid.UUID
    channel_workspace_id: uuid.UUID
    content_category_id: uuid.UUID | None
    series_id: uuid.UUID | None
    character_profile_id: uuid.UUID | None
    character_version_id: uuid.UUID | None
    agent_key: str
    use_case: str
    query_facet_type: str | None
    query_text_hash: str
    sql_filter_json: dict[str, Any]
    candidate_count_before_vector: int
    candidate_count_after_policy: int
    selected_memory_facet_refs_json: list[dict[str, Any]]
    blocked_refs_json: list[dict[str, Any]]
    rejected_refs_json: list[dict[str, Any]]
    vector_model: str | None
    ranking_params_json: dict[str, Any]
    retrieval_hash: str
    digest_hash: str | None
    created_at: AwareDatetime

    model_config = ConfigDict(extra="forbid", from_attributes=True)

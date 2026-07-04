from fastapi import APIRouter

from app.api.routes.imports import (
    BudgetGateCheckRequest,
    BudgetGateDecisionRead,
    BudgetGateService,
    BudgetPolicyCreate,
    BudgetPolicyRead,
    ComponentHealthService,
    ComponentHealthSnapshotCreate,
    ComponentHealthSnapshotRead,
    CostEventCreate,
    CostEventRead,
    CostService,
    CredentialHealthSnapshotCreate,
    CredentialHealthSnapshotRead,
    CredentialReferenceCreate,
    CredentialReferenceRead,
    CredentialReferenceService,
    DeadLetterJobCreate,
    DeadLetterJobRead,
    DeadLetterService,
    ManualActionCreate,
    ManualActionRead,
    ManualActionService,
    NotFoundError,
    OpsIncidentCreate,
    OpsIncidentRead,
    OpsIncidentService,
    ProviderAttemptRead,
    ProviderHealthCheckRequest,
    ProviderHealthService,
    ProviderHealthSnapshotRead,
    ProviderRegistryEntryCreate,
    ProviderRegistryEntryRead,
    ProviderRegistryService,
    QuotaAccountCreate,
    QuotaAccountRead,
    QuotaEventRead,
    QuotaEventRequest,
    QuotaService,
    RetryOpsService,
    RetryPolicyCreate,
    RetryPolicyRead,
    SystemHealthService,
    SystemHealthSnapshotRead,
    session_scope,
    uuid,
)

from app.api.routes.serializers_core import (
    _budget_policy,
    _component_health,
    _cost_event,
    _credential_health,
    _credential_reference,
    _dead_letter_job,
    _manual_action,
    _ops_incident,
    _provider_attempt,
    _provider_health,
    _provider_registry_entry,
    _quota_account,
    _quota_event,
    _retry_policy,
    _system_health,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/providers", response_model=ProviderRegistryEntryRead)
    def create_provider(data: ProviderRegistryEntryCreate) -> ProviderRegistryEntryRead:
        try:
            with session_scope() as session:
                entry = ProviderRegistryService(session).create_entry(data=data)
                return ProviderRegistryEntryRead.model_validate(_provider_registry_entry(entry))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/providers", response_model=list[ProviderRegistryEntryRead])
    def list_providers() -> list[ProviderRegistryEntryRead]:
        try:
            with session_scope() as session:
                return [
                    ProviderRegistryEntryRead.model_validate(_provider_registry_entry(entry))
                    for entry in ProviderRegistryService(session).list_entries()
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/providers/{provider_key}", response_model=ProviderRegistryEntryRead)
    def get_provider(provider_key: str) -> ProviderRegistryEntryRead:
        try:
            with session_scope() as session:
                entry = ProviderRegistryService(session).require_entry(provider_key)
                return ProviderRegistryEntryRead.model_validate(_provider_registry_entry(entry))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/providers/{provider_key}/health-check", response_model=ProviderHealthSnapshotRead)
    def provider_health_check(provider_key: str, data: ProviderHealthCheckRequest) -> ProviderHealthSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ProviderHealthService(session).check_provider(
                    provider_key=provider_key,
                    mode=data.mode,
                    next_action=data.next_action,
                    metadata=data.metadata,
                )
                return ProviderHealthSnapshotRead.model_validate(_provider_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/providers/{provider_key}/health", response_model=list[ProviderHealthSnapshotRead])
    def list_provider_health(provider_key: str) -> list[ProviderHealthSnapshotRead]:
        try:
            with session_scope() as session:
                return [ProviderHealthSnapshotRead.model_validate(_provider_health(item)) for item in ProviderHealthService(session).list_health(provider_key)]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/credential-references", response_model=CredentialReferenceRead)
    def create_credential_reference(data: CredentialReferenceCreate) -> CredentialReferenceRead:
        try:
            with session_scope() as session:
                reference = CredentialReferenceService(session).create_reference(data=data)
                return CredentialReferenceRead.model_validate(_credential_reference(reference))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/credential-references/{credential_reference_id}", response_model=CredentialReferenceRead)
    def get_credential_reference(credential_reference_id: uuid.UUID) -> CredentialReferenceRead:
        try:
            with session_scope() as session:
                reference = CredentialReferenceService(session).require_reference(credential_reference_id)
                return CredentialReferenceRead.model_validate(_credential_reference(reference))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/credential-references/{credential_reference_id}/health-check", response_model=CredentialHealthSnapshotRead)
    def credential_health_check(credential_reference_id: uuid.UUID, data: CredentialHealthSnapshotCreate | None = None) -> CredentialHealthSnapshotRead:
        try:
            with session_scope() as session:
                request = data or CredentialHealthSnapshotCreate(credential_reference_id=credential_reference_id)
                request = request.model_copy(update={"credential_reference_id": credential_reference_id})
                snapshot = CredentialReferenceService(session).check_health(data=request)
                return CredentialHealthSnapshotRead.model_validate(_credential_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/quota-accounts", response_model=QuotaAccountRead)
    def create_quota_account(data: QuotaAccountCreate) -> QuotaAccountRead:
        try:
            with session_scope() as session:
                account = QuotaService(session).create_account(data=data)
                return QuotaAccountRead.model_validate(_quota_account(account))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/quota-accounts/{quota_account_id}", response_model=QuotaAccountRead)
    def get_quota_account(quota_account_id: uuid.UUID) -> QuotaAccountRead:
        try:
            with session_scope() as session:
                account = QuotaService(session).require_account(quota_account_id)
                return QuotaAccountRead.model_validate(_quota_account(account))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/quota-events/reserve", response_model=QuotaEventRead)
    def reserve_quota(data: QuotaEventRequest) -> QuotaEventRead:
        try:
            with session_scope() as session:
                event = QuotaService(session).reserve_quota(data=data)
                return QuotaEventRead.model_validate(_quota_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/quota-events/consume", response_model=QuotaEventRead)
    def consume_quota(data: QuotaEventRequest) -> QuotaEventRead:
        try:
            with session_scope() as session:
                event = QuotaService(session).consume_quota(data=data)
                return QuotaEventRead.model_validate(_quota_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/quota-events/release", response_model=QuotaEventRead)
    def release_quota(data: QuotaEventRequest) -> QuotaEventRead:
        try:
            with session_scope() as session:
                event = QuotaService(session).release_quota(data=data)
                return QuotaEventRead.model_validate(_quota_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/cost-events", response_model=CostEventRead)
    def create_cost_event(data: CostEventCreate) -> CostEventRead:
        try:
            with session_scope() as session:
                event = CostService(session).record_event(data=data)
                return CostEventRead.model_validate(_cost_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/cost-events", response_model=list[CostEventRead])
    def list_cost_events(
        provider_key: str | None = None,
        cost_scope_type: str | None = None,
        cost_scope_id: uuid.UUID | None = None,
    ) -> list[CostEventRead]:
        try:
            with session_scope() as session:
                return [
                    CostEventRead.model_validate(_cost_event(event))
                    for event in CostService(session).list_events(
                        provider_key=provider_key,
                        cost_scope_type=cost_scope_type,
                        cost_scope_id=cost_scope_id,
                    )
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/budget-policies", response_model=BudgetPolicyRead)
    def create_budget_policy(data: BudgetPolicyCreate) -> BudgetPolicyRead:
        try:
            with session_scope() as session:
                policy = BudgetGateService(session).create_policy(data=data)
                return BudgetPolicyRead.model_validate(_budget_policy(policy))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/budget-gates/check", response_model=BudgetGateDecisionRead)
    def check_budget_gate(data: BudgetGateCheckRequest) -> BudgetGateDecisionRead:
        try:
            with session_scope() as session:
                return BudgetGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/component-health/snapshot", response_model=ComponentHealthSnapshotRead)
    def create_component_health(data: ComponentHealthSnapshotCreate) -> ComponentHealthSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ComponentHealthService(session).create_snapshot(data=data)
                return ComponentHealthSnapshotRead.model_validate(_component_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/system-health/snapshot", response_model=SystemHealthSnapshotRead)
    def create_system_health_snapshot() -> SystemHealthSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = SystemHealthService(session).create_snapshot()
                return SystemHealthSnapshotRead.model_validate(_system_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/system-health/latest", response_model=SystemHealthSnapshotRead | None)
    def get_latest_system_health() -> SystemHealthSnapshotRead | None:
        try:
            with session_scope() as session:
                snapshot = SystemHealthService(session).latest()
                return SystemHealthSnapshotRead.model_validate(_system_health(snapshot)) if snapshot is not None else None
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/retry-policies", response_model=RetryPolicyRead)
    def create_retry_policy(data: RetryPolicyCreate) -> RetryPolicyRead:
        try:
            with session_scope() as session:
                policy = RetryOpsService(session).create_policy(data=data)
                return RetryPolicyRead.model_validate(_retry_policy(policy))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/provider-attempts/{attempt_id}", response_model=ProviderAttemptRead)
    def get_provider_attempt(attempt_id: uuid.UUID) -> ProviderAttemptRead:
        try:
            with session_scope() as session:
                attempt = RetryOpsService(session).get_attempt(attempt_id)
                if attempt is None:
                    raise NotFoundError(f"provider attempt not found: {attempt_id}")
                return ProviderAttemptRead.model_validate(_provider_attempt(attempt))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/dead-letter-jobs", response_model=DeadLetterJobRead)
    def create_dead_letter_job(data: DeadLetterJobCreate) -> DeadLetterJobRead:
        try:
            with session_scope() as session:
                job = DeadLetterService(session).create_job(data=data)
                return DeadLetterJobRead.model_validate(_dead_letter_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/dead-letter-jobs/{job_id}/replay", response_model=DeadLetterJobRead)
    def replay_dead_letter_job(job_id: uuid.UUID) -> DeadLetterJobRead:
        try:
            with session_scope() as session:
                job = DeadLetterService(session).replay_job(job_id)
                return DeadLetterJobRead.model_validate(_dead_letter_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/ops-incidents", response_model=OpsIncidentRead)
    def create_ops_incident(data: OpsIncidentCreate) -> OpsIncidentRead:
        try:
            with session_scope() as session:
                incident = OpsIncidentService(session).create_incident(data=data)
                return OpsIncidentRead.model_validate(_ops_incident(incident))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/ops-incidents", response_model=list[OpsIncidentRead])
    def list_ops_incidents() -> list[OpsIncidentRead]:
        try:
            with session_scope() as session:
                return [OpsIncidentRead.model_validate(_ops_incident(item)) for item in OpsIncidentService(session).list_incidents()]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/ops-incidents/{incident_id}/acknowledge", response_model=OpsIncidentRead)
    def acknowledge_ops_incident(incident_id: uuid.UUID) -> OpsIncidentRead:
        try:
            with session_scope() as session:
                incident = OpsIncidentService(session).transition(incident_id, "ACKNOWLEDGED")
                return OpsIncidentRead.model_validate(_ops_incident(incident))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/ops-incidents/{incident_id}/resolve", response_model=OpsIncidentRead)
    def resolve_ops_incident(incident_id: uuid.UUID) -> OpsIncidentRead:
        try:
            with session_scope() as session:
                incident = OpsIncidentService(session).transition(incident_id, "RESOLVED")
                return OpsIncidentRead.model_validate(_ops_incident(incident))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/manual-actions", response_model=ManualActionRead)
    def create_manual_action(data: ManualActionCreate) -> ManualActionRead:
        try:
            with session_scope() as session:
                action = ManualActionService(session).create_action(data=data)
                return ManualActionRead.model_validate(_manual_action(action))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/manual-actions", response_model=list[ManualActionRead])
    def list_manual_actions() -> list[ManualActionRead]:
        try:
            with session_scope() as session:
                return [ManualActionRead.model_validate(_manual_action(item)) for item in ManualActionService(session).list_actions()]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/manual-actions/{action_id}/complete", response_model=ManualActionRead)
    def complete_manual_action(action_id: uuid.UUID) -> ManualActionRead:
        try:
            with session_scope() as session:
                action = ManualActionService(session).complete_action(action_id)
                return ManualActionRead.model_validate(_manual_action(action))
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router

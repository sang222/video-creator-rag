from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.contracts.launch_cadence import CadenceDecision, CadenceEvaluationCommand
from app.core.actor import _system_worker_actor
from app.core.errors import ValidationFailureError
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m5 import (
    EditorialIdeaCandidate,
    IdeaMarketPreflight,
    SearchDemandEvidence,
)
from app.db.models.ops import ProviderAttempt
from app.db.models.script_qualification import (
    EditorialTopicDefinition,
    EditorialTopicDefinitionGateReceipt,
    ScriptContractReplacementAuthority,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationRun,
)
from app.services.canonical_script_compiler import SCRIPT_CONTRACT_V2
from app.services.config_registry import content_hash
from app.services.editorial_novelty import (
    EDITORIAL_NOVELTY_GATE_VERSION,
    EditorialNoveltyService,
)
from app.services.editorial_specificity import (
    EDITORIAL_SPECIFICITY_GATE_VERSION,
    EditorialSpecificityService,
)
from app.services.launch_cadence import LongFormCadenceService
from app.services.ops import ProviderRegistryService
from app.services.m5 import SearchDemandEvidenceService
from app.services.script_contract_replacement import (
    OPERATOR_RECOVERY_REASON,
    OPERATOR_RECOVERY_SCHEMA,
    OPERATOR_RECOVERY_STRATEGY,
    ScriptContractReplacementAuthorityService,
)
from app.services.scoped_replacement_runner import (
    ScopedReplacementContinuationRunner,
)
from app.services.script_qualification import SCRIPT_CONTRACT_V1
from app.services.script_qualification_background import (
    build_script_qualification_deadline_policy,
    script_qualification_slot_is_viable,
)
from tests.qualification.conftest import QualificationFactory
from tests.test_long_form_launch_cadence import (
    _active_launch_run,
    _actor,
    _approved_launch_policy,
    _greenlit_candidate,
    _ready_provider_snapshot,
    _test_support_authority_preparer,
)


@pytest.fixture
def qualification_factory(db_session):
    return QualificationFactory(db_session)


def _controlled_actor():
    return _system_worker_actor(
        "vcos-controlled-recovery",
        permissions={"editorial.manage", "production.start"},
    )


def _ready_authorities(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.script_contract_replacement.resolve_budget_authority",
        lambda *_args, **_kwargs: {"state": "READY"},
    )
    monkeypatch.setattr(
        "app.services.script_contract_replacement.resolve_provider_authority",
        lambda *_args, **_kwargs: {"state": "READY"},
    )


def _bind_fresh_evidence_authority(
    session,
    *,
    scope,
    candidate: EditorialIdeaCandidate,
    freshness_days: int = 30,
) -> SearchDemandEvidence:
    registry = ProviderRegistryService(session)
    provider = registry.require_entry("openai")
    provider.capability_blob = {
        **(provider.capability_blob or {}),
        "editorial_evidence_collection": True,
    }
    policy = {
        "schema_version": "test-controlled-recovery-evidence.v1",
        "policy_snapshot_id": str(scope.snapshot.id),
        "policy_snapshot_hash": scope.snapshot.content_hash,
        "network_access_allowed": True,
        "allowed_source_classes": ["OFFICIAL_DOCUMENT"],
        "allowed_domains": ["docs.example.test"],
        "maximum_sources_per_run": 3,
        "timeout_seconds": 10,
        "freshness_days": freshness_days,
        "automatic_fallback": False,
    }
    policy["config_hash"] = content_hash(policy)
    provider.policy_fit_blob = {
        **(provider.policy_fit_blob or {}),
        "editorial_evidence_authority": policy,
    }

    evidence_id = uuid.UUID(str(candidate.evidence_refs[0]["id"]))
    evidence = session.get(SearchDemandEvidence, evidence_id)
    assert evidence is not None
    session.flush()
    return evidence


def _expired_zero_effect_source(
    session,
    qualification_factory,
    *,
    recovery_now: datetime,
    name: str,
    freshness_days: int = 30,
    evidence_retrieved_at: datetime | None = None,
) -> SimpleNamespace:
    scope = qualification_factory.channel_scope(
        name=name,
        strict_long_form=True,
    )
    policy, admin_actor, _ = _approved_launch_policy(
        session,
        scope,
        timezone_name="UTC",
        weekdays=["TUESDAY"],
    )
    launch = _active_launch_run(
        session,
        policy,
        admin_actor,
        started_on=date(2026, 7, 20),
    )
    retrieved_at = (
        evidence_retrieved_at
        if evidence_retrieved_at is not None
        else recovery_now - timedelta(days=2)
    )
    original_create_evidence = SearchDemandEvidenceService.create_evidence

    def create_timestamped_evidence(
        service,
        *,
        data,
        correlation_id="m5-search-demand-evidence",
    ):
        if data.authority_purpose == "CLAIM_SOURCE":
            metadata = deepcopy(data.metadata)
            metadata["editorial_fresh_evidence"]["source_snapshot"][
                "retrieved_at"
            ] = retrieved_at.isoformat()
            data = data.model_copy(
                update={"captured_at": retrieved_at, "metadata": metadata}
            )
        return original_create_evidence(
            service,
            data=data,
            correlation_id=correlation_id,
        )

    with patch.object(
        SearchDemandEvidenceService,
        "create_evidence",
        new=create_timestamped_evidence,
    ):
        _, candidate, source_preflight = _greenlit_candidate(
            session,
            scope,
            _actor(session, scope),
        )
    source_topic = session.scalar(
        select(EditorialTopicDefinition).where(
            EditorialTopicDefinition.editorial_idea_candidate_id == candidate.id
        )
    )
    source_topic_gate = session.scalar(
        select(EditorialTopicDefinitionGateReceipt).where(
            EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id
            == source_topic.id
        )
    )
    assert source_topic is not None and source_topic_gate is not None

    source_time = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
    cadence = LongFormCadenceService(
        session,
        now=lambda: source_time,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    cadence_receipt = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(
            evaluation_key=f"controlled-recovery-source:{name}"
        ),
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )
    assert cadence_receipt.decision == CadenceDecision.START_SCRIPT_QUALIFICATION
    assert cadence_receipt.script_qualification_run_id is not None
    source_run = session.get(
        ScriptQualificationRun,
        cadence_receipt.script_qualification_run_id,
    )
    source_slot = session.get(LongFormPublishSlot, cadence_receipt.publish_slot_id)
    assert source_run is not None and source_slot is not None
    assert source_run.script_contract_version == SCRIPT_CONTRACT_V1
    assert source_slot.state == "QUALIFICATION_RESERVED"
    assert not list(
        session.scalars(
            select(ScriptQualificationBackgroundAttempt).where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == source_run.id
            )
        ).all()
    )

    source_run.state = "BLOCKED_NON_REPAIRABLE"
    source_run.failure_receipt = {
        "schema_version": "script-qualification-failure.v1",
        "reason_codes": ["SCRIPT_PROVIDER_LOGICAL_DEADLINE_EXCEEDED"],
        "detail": "Historical reservation reached its logical deadline before submission.",
    }
    source_run.logical_deadline_at = min(
        source_run.logical_deadline_at,
        recovery_now - timedelta(hours=1),
    )
    evidence = _bind_fresh_evidence_authority(
        session,
        scope=scope,
        candidate=candidate,
        freshness_days=freshness_days,
    )
    session.flush()
    return SimpleNamespace(
        scope=scope,
        policy=policy,
        launch=launch,
        candidate=candidate,
        source_run=source_run,
        source_slot=source_slot,
        source_topic=source_topic,
        source_topic_gate=source_topic_gate,
        source_preflight=source_preflight,
        evidence=evidence,
    )


def _historical_snapshot(source: SimpleNamespace) -> dict:
    return {
        "candidate": {
            "stage": source.candidate.stage,
            "canonical_hash": source.candidate.canonical_hash,
            "reason_codes": deepcopy(source.candidate.reason_codes),
            "replacement_authority_id": source.candidate.replacement_authority_id,
        },
        "qualification": {
            "state": source.source_run.state,
            "failure_receipt": deepcopy(source.source_run.failure_receipt),
            "logical_deadline_at": source.source_run.logical_deadline_at,
            "terminal_settlement_receipt": deepcopy(
                source.source_run.terminal_settlement_receipt
            ),
        },
        "slot": {
            "state": source.source_slot.state,
            "reserved_candidate_id": source.source_slot.reserved_candidate_id,
            "admitted_video_project_id": source.source_slot.admitted_video_project_id,
            "target_start_window_close_at": (
                source.source_slot.target_start_window_close_at
            ),
        },
    }


def test_controlled_recovery_creates_one_fresh_v2_lineage_and_sealed_receipt(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    recovery_now = datetime(2026, 8, 12, 15, tzinfo=timezone.utc)
    source = _expired_zero_effect_source(
        db_session,
        qualification_factory,
        recovery_now=recovery_now,
        name="Controlled recovery success",
    )
    historical = _historical_snapshot(source)
    _ready_authorities(monkeypatch)

    service = ScriptContractReplacementAuthorityService(
        db_session,
        now=lambda: recovery_now,
    )
    lineage = service.create_operator_first_video_recovery(
        historical_candidate_id=source.candidate.id,
        actor=_controlled_actor(),
    )

    assert _historical_snapshot(source) == historical
    assert lineage.authority.replaces_candidate_id == source.candidate.id
    assert lineage.authority.replaces_slot_id == source.source_slot.id
    assert lineage.authority.historical_qualification_id == source.source_run.id
    assert lineage.authority.replacement_reason == OPERATOR_RECOVERY_REASON
    assert lineage.authority.operator_recovery_schema_version == OPERATOR_RECOVERY_SCHEMA
    assert lineage.authority.recovery_strategy == OPERATOR_RECOVERY_STRATEGY
    assert lineage.authority.operator_recovery_id == lineage.authority.id
    assert lineage.authority.operator_actor_context == {
        "actor_type": "SYSTEM_WORKER",
        "actor_id": str(_controlled_actor().actor_id),
        "actor_role": "SYSTEM_WORKER",
        "operator_user_id": None,
    }

    assert lineage.candidate.id != source.candidate.id
    assert lineage.candidate.replaces_candidate_id == source.candidate.id
    assert lineage.candidate.stage == "GREENLIT"
    assert lineage.candidate.script_contract_version == SCRIPT_CONTRACT_V2
    assert lineage.slot.id != source.source_slot.id
    assert lineage.slot.replaces_slot_id == source.source_slot.id
    assert lineage.slot.state == "QUALIFICATION_RESERVED"
    assert lineage.slot.reserved_candidate_id == lineage.candidate.id
    assert lineage.slot.target_start_window_open_at == recovery_now
    assert lineage.qualification.id != source.source_run.id
    assert lineage.qualification.editorial_idea_candidate_id == lineage.candidate.id
    assert lineage.qualification.publish_slot_id == lineage.slot.id
    assert lineage.qualification.script_contract_version == SCRIPT_CONTRACT_V2
    assert lineage.qualification.state == "RESERVED"

    deadline_policy = build_script_qualification_deadline_policy()
    assert script_qualification_slot_is_viable(
        recovery_now,
        lineage.slot.target_start_window_close_at,
        deadline_policy,
    )
    assert lineage.qualification.logical_deadline_at == (
        lineage.authority.qualification_deadline
    )
    assert lineage.qualification.logical_deadline_at > recovery_now
    assert (
        lineage.qualification.logical_deadline_at - recovery_now
    ).total_seconds() >= deadline_policy.total_qualification_budget_seconds
    assert lineage.slot.intended_publish_at == (
        lineage.slot.target_start_window_close_at
        + timedelta(hours=source.policy.render_lead_time_min_hours)
    )

    replacement_topic = db_session.scalar(
        select(EditorialTopicDefinition).where(
            EditorialTopicDefinition.editorial_idea_candidate_id
            == lineage.candidate.id
        )
    )
    replacement_topic_gate = db_session.scalar(
        select(EditorialTopicDefinitionGateReceipt).where(
            EditorialTopicDefinitionGateReceipt.editorial_topic_definition_id
            == replacement_topic.id
        )
    )
    replacement_preflight = db_session.scalar(
        select(IdeaMarketPreflight).where(
            IdeaMarketPreflight.editorial_idea_candidate_id == lineage.candidate.id
        )
    )
    assert replacement_topic is not None and replacement_topic.id != source.source_topic.id
    assert replacement_topic.parent_topic_definition_id == source.source_topic.id
    assert replacement_topic_gate is not None
    assert replacement_topic_gate.id != source.source_topic_gate.id
    assert replacement_topic_gate.state == "PASS"
    assert replacement_topic_gate.current_production_eligibility is True
    assert replacement_preflight is not None
    assert replacement_preflight.id != source.source_preflight.id
    assert replacement_preflight.decision == "PASS"
    assert replacement_preflight.policy_fit_state == "PASS"
    assert EditorialSpecificityService(db_session).current_pass(lineage.candidate)
    assert lineage.candidate.editorial_specificity_receipt["gate_version"] == (
        EDITORIAL_SPECIFICITY_GATE_VERSION
    )
    current_novelty = EditorialNoveltyService(db_session).evaluate(
        candidate=lineage.candidate,
        topic=replacement_topic,
    )
    assert current_novelty.state == "PASS"
    assert current_novelty.gate_version == EDITORIAL_NOVELTY_GATE_VERSION
    assert lineage.candidate.editorial_novelty_receipt == current_novelty.receipt()

    freshness = lineage.authority.freshness_snapshot
    assert freshness["state"] == "FRESH"
    assert freshness["sources"] == [
        {
            **freshness["sources"][0],
            "evidence_id": str(source.evidence.id),
            "state": "FRESH",
        }
    ]
    assert freshness["snapshot_hash"] == content_hash(
        {key: value for key, value in freshness.items() if key != "snapshot_hash"}
    )
    receipt_body = {
        "schema_version": OPERATOR_RECOVERY_SCHEMA,
        "operator_recovery_id": str(lineage.authority.id),
        "replacement_candidate_id": str(lineage.candidate.id),
        "historical_candidate_id": str(source.candidate.id),
        "historical_qualification_id": str(source.source_run.id),
        "historical_slot_id": str(source.source_slot.id),
        "reason": OPERATOR_RECOVERY_REASON,
        "recovery_strategy": OPERATOR_RECOVERY_STRATEGY,
        "authority_versions": lineage.authority.authority_versions,
        "freshness_snapshot": freshness,
        "actor_context": lineage.authority.operator_actor_context,
        "created_at": recovery_now.isoformat(),
    }
    assert lineage.authority.recovery_receipt_hash == content_hash(receipt_body)
    assert len(lineage.authority.recovery_receipt_hash) == 64
    assert lineage.authority.authority_versions["script_contract"] == (
        SCRIPT_CONTRACT_V2
    )
    assert lineage.authority.authority_versions["deadline_policy"]

    repeated = service.create_operator_first_video_recovery(
        historical_candidate_id=source.candidate.id,
        actor=_controlled_actor(),
    )
    assert repeated.authority.id == lineage.authority.id
    assert repeated.candidate.id == lineage.candidate.id
    assert repeated.slot.id == lineage.slot.id
    assert repeated.qualification.id == lineage.qualification.id
    assert db_session.scalar(
        select(func.count(ScriptContractReplacementAuthority.id)).where(
            ScriptContractReplacementAuthority.replacement_reason
            == OPERATOR_RECOVERY_REASON
        )
    ) == 1
    assert db_session.scalar(
        select(func.count(EditorialIdeaCandidate.id)).where(
            EditorialIdeaCandidate.replaces_candidate_id == source.candidate.id
        )
    ) == 1
    assert db_session.scalar(
        select(func.count(LongFormPublishSlot.id)).where(
            LongFormPublishSlot.replaces_slot_id == source.source_slot.id,
            LongFormPublishSlot.replacement_reason == OPERATOR_RECOVERY_REASON,
        )
    ) == 1
    assert db_session.scalar(select(func.count(ProviderAttempt.id))) == 0


def test_controlled_recovery_rejects_every_non_recovery_actor(
    db_session,
) -> None:
    actor = _system_worker_actor(
        "vcos-durable-worker",
        permissions={"editorial.manage", "production.start"},
    )

    with pytest.raises(
        ValidationFailureError,
        match="CONTROLLED_RECOVERY_ACTOR_UNTRUSTED",
    ):
        ScriptContractReplacementAuthorityService(db_session).create_operator_first_video_recovery(
            historical_candidate_id=uuid.uuid4(),
            actor=actor,
        )


def test_controlled_recovery_rejects_expired_evidence_before_creating_lineage(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    recovery_now = datetime(2026, 8, 12, 15, tzinfo=timezone.utc)
    source = _expired_zero_effect_source(
        db_session,
        qualification_factory,
        recovery_now=recovery_now,
        name="Controlled recovery stale evidence",
        freshness_days=7,
        evidence_retrieved_at=recovery_now - timedelta(days=8),
    )
    historical = _historical_snapshot(source)
    _ready_authorities(monkeypatch)

    with pytest.raises(
        ValidationFailureError,
        match="CONTROLLED_RECOVERY_EVIDENCE_NOT_CURRENT_FRESH",
    ):
        ScriptContractReplacementAuthorityService(
            db_session,
            now=lambda: recovery_now,
        ).create_operator_first_video_recovery(
            historical_candidate_id=source.candidate.id,
            actor=_controlled_actor(),
        )

    assert _historical_snapshot(source) == historical
    assert db_session.scalar(
        select(func.count(ScriptContractReplacementAuthority.id)).where(
            ScriptContractReplacementAuthority.replacement_reason
            == OPERATOR_RECOVERY_REASON
        )
    ) == 0
    assert db_session.scalar(
        select(func.count(EditorialIdeaCandidate.id)).where(
            EditorialIdeaCandidate.replaces_candidate_id == source.candidate.id
        )
    ) == 0


def test_controlled_recovery_rejects_ambiguous_provider_submission(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    recovery_now = datetime(2026, 8, 12, 15, tzinfo=timezone.utc)
    source = _expired_zero_effect_source(
        db_session,
        qualification_factory,
        recovery_now=recovery_now,
        name="Controlled recovery ambiguous provider submission",
    )
    historical = _historical_snapshot(source)
    _ready_authorities(monkeypatch)
    db_session.add(
        ScriptQualificationBackgroundAttempt(
            script_qualification_run_id=source.source_run.id,
            phase="WRITER",
            provider="OPENAI",
            model=source.source_run.model,
            lane="script_qualification.writer",
            task="script_qualification.writer",
            input_fingerprint="a" * 64,
            immutable_input_hashes={},
            client_correlation_id=f"ambiguous:{source.source_run.id}",
            provider_request_id="request-outcome-unknown",
            background_status="SUBMISSION_OUTCOME_UNKNOWN",
            provider_outcome="SUBMISSION_OUTCOME_UNKNOWN",
            logical_deadline_at=source.source_run.logical_deadline_at,
            poll_count=0,
            submission_attempt_count=1,
        )
    )
    db_session.flush()

    with pytest.raises(
        ValidationFailureError,
        match="CONTROLLED_RECOVERY_EXPIRED_ZERO_EFFECT_LINEAGE_REQUIRED",
    ):
        ScriptContractReplacementAuthorityService(
            db_session,
            now=lambda: recovery_now,
        ).create_operator_first_video_recovery(
            historical_candidate_id=source.candidate.id,
            actor=_controlled_actor(),
        )

    assert _historical_snapshot(source) == historical
    assert db_session.scalar(
        select(func.count(ScriptContractReplacementAuthority.id)).where(
            ScriptContractReplacementAuthority.replacement_reason
            == OPERATOR_RECOVERY_REASON
        )
    ) == 0


def test_controlled_recovery_rejects_future_evidence_timestamp(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    recovery_now = datetime(2026, 8, 12, 15, tzinfo=timezone.utc)
    source = _expired_zero_effect_source(
        db_session,
        qualification_factory,
        recovery_now=recovery_now,
        name="Controlled recovery future evidence",
        evidence_retrieved_at=recovery_now + timedelta(seconds=1),
    )
    historical = _historical_snapshot(source)
    _ready_authorities(monkeypatch)

    with pytest.raises(
        ValidationFailureError,
        match="CONTROLLED_RECOVERY_EVIDENCE_TIMESTAMP_IN_FUTURE",
    ):
        ScriptContractReplacementAuthorityService(
            db_session,
            now=lambda: recovery_now,
        ).create_operator_first_video_recovery(
            historical_candidate_id=source.candidate.id,
            actor=_controlled_actor(),
        )

    assert _historical_snapshot(source) == historical


def test_no_hold_continuation_rejects_legacy_replacement_authority(
    db_session,
) -> None:
    legacy_authority = SimpleNamespace(
        id=uuid.uuid4(),
        replacement_qualification_run_id=uuid.uuid4(),
        replacement_reason="SCRIPT_CONTRACT_SINGLE_SOURCE_OF_TRUTH_MIGRATION",
        operator_recovery_schema_version=None,
    )
    runner = ScopedReplacementContinuationRunner(
        db_session,
        now=lambda: datetime(2026, 8, 12, 15, tzinfo=timezone.utc),
        post_render_hold_requested=False,
    )

    with patch.object(db_session, "scalar", return_value=legacy_authority):
        with pytest.raises(
            ValidationFailureError,
            match="SCOPED_REPLACEMENT_FINAL_BOUNDARY_NOT_AUTHORIZED",
        ):
            runner.run_once(authority_id=legacy_authority.id)

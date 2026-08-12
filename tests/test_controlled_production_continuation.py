from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import ProgrammingError

from app.contracts.script_qualification import QualifiedScriptOutputV2
from app.core.errors import ValidationFailureError
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.script_qualification import (
    ControlledProductionContinuationAuthority,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationRun,
)
from app.services.config_registry import content_hash
from app.services.runtime_migration_guard import (
    REQUIRED_RUNTIME_DB_REVISION,
    RuntimeMigrationGuard,
)
from app.services.script_content_repair import ScriptContentRepairService
from app.services.script_contract_replacement import (
    CONTROLLED_CONTINUATION_REASON,
    CONTROLLED_CONTINUATION_SCHEMA,
    ScriptContractReplacementAuthorityService,
    controlled_continuation_authority_body,
    resolve_replacement_qualification_leaf,
)
from app.services.script_qualification import ScriptQualificationService
from app.services.script_qualification_background import (
    ScriptQualificationBackgroundService,
)
from app.services.script_qualification_recovery import (
    ScriptQualificationRecoveryService,
)
from app.services.script_writer_output_recovery import (
    ScriptWriterOutputRecoveryService,
)
from tests.qualification.conftest import QualificationFactory
from tests.test_controlled_production_recovery import (
    _controlled_actor,
    _expired_zero_effect_source,
    _ready_authorities,
)


@pytest.fixture
def qualification_factory(db_session):
    return QualificationFactory(db_session)


def _ready_snapshot() -> dict[str, str]:
    return {"state": "READY", "authority": "focused-continuation-test"}


def _patch_current_authorities(monkeypatch) -> None:
    for target in (
        "app.services.script_writer_output_recovery.resolve_provider_authority",
        "app.services.script_writer_output_recovery.resolve_budget_authority",
        "app.services.production_start_readiness.resolve_provider_authority",
        "app.services.production_start_readiness.resolve_budget_authority",
    ):
        monkeypatch.setattr(target, lambda *_args, **_kwargs: _ready_snapshot())
    monkeypatch.setattr(
        "app.services.script_content_repair.resolve_budget_authority",
        lambda *_args, **_kwargs: _ready_snapshot(),
    )


def _v2_payload(run: ScriptQualificationRun, *, repaired: bool = False) -> dict:
    plan = (run.script_assignment or {})["section_coverage_plan"]
    sections = plan["sections"]
    target_words = int(plan["target_word_count"])
    base_words, remainder = divmod(target_words, len(sections))
    evidence_span_id = run.factual_evidence_pack["spans"][0]["evidence_span_id"]
    output_sections: list[dict] = []
    claims: list[dict] = []
    for index, coverage in enumerate(sections):
        ordinal = int(coverage["ordinal"])
        word_count = base_words + (1 if index < remainder else 0)
        variant = "repaired" if repaired and ordinal == 1 else "original"
        narration = (
            " ".join(
                f"{variant}section{ordinal}token{token}"
                for token in range(1, word_count + 1)
            )
            + "."
        )
        claim_id = f"claim-{ordinal:03d}"
        output_sections.append(
            {
                "section_id": coverage["section_id"],
                "ordinal": ordinal,
                "purpose": coverage["section_delta"],
                "narration": narration,
                "required_assignment_unit_refs": coverage["primary_requirement_ids"],
                "expected_claim_refs": [claim_id],
            }
        )
        claims.append(
            {
                "claim_id": claim_id,
                "claim_text": narration,
                "evidence_span_ids": [evidence_span_id],
            }
        )
    return QualifiedScriptOutputV2(
        language=(run.runtime_contract or {}).get("expected_language") or "en",
        sections=output_sections,
        claims=claims,
    ).model_dump(mode="json")


def _completed_attempt_with_snapshot(
    session,
    *,
    run: ScriptQualificationRun,
    phase: str,
    payload: dict,
    provider_outcome: str,
    prompt_version: str,
    identity: str,
) -> tuple[
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
]:
    response_id = f"response-{identity}-{uuid.uuid4()}"
    request_id = f"request-{identity}-{uuid.uuid4()}"
    schema_hash = content_hash({"schema": identity})
    input_fingerprint = content_hash({"input": identity, "run_id": str(run.id)})
    raw_provider_response = {
        "id": response_id,
        "status": "completed",
        "output": [{"content": payload}],
    }
    raw_provider_response_hash = content_hash(raw_provider_response)
    raw_output_content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    attempt = ScriptQualificationBackgroundAttempt(
        script_qualification_run_id=run.id,
        phase=phase,
        provider="OPENAI",
        model=run.model,
        lane=f"script_qualification.{phase.casefold()}",
        task=f"script_qualification.{phase.casefold()}",
        input_fingerprint=input_fingerprint,
        immutable_input_hashes={
            "script_assignment_hash": run.script_assignment_hash,
            "factual_evidence_pack_hash": run.factual_evidence_pack_hash,
        },
        client_correlation_id=f"{identity}:{run.id}",
        provider_response_id=response_id,
        provider_request_id=request_id,
        background_status="COMPLETED",
        provider_outcome=provider_outcome,
        submitted_at=run.created_at,
        completed_at=run.created_at,
        logical_deadline_at=run.logical_deadline_at,
        poll_count=1,
        submission_attempt_count=1,
        output_hash=raw_provider_response_hash,
        response_schema_identifier=f"schema-{identity}",
        response_schema_hash=schema_hash,
        prompt_version=prompt_version,
    )
    session.add(attempt)
    session.flush()
    snapshot = ScriptQualificationProviderResponseSnapshot(
        script_qualification_run_id=run.id,
        background_attempt_id=attempt.id,
        phase=phase,
        provider_response_id=response_id,
        provider_request_id=request_id,
        raw_provider_response=raw_provider_response,
        raw_provider_response_hash=raw_provider_response_hash,
        raw_output_content=raw_output_content,
        raw_output_hash=content_hash({"content": raw_output_content}),
        response_schema_identifier=attempt.response_schema_identifier,
        response_schema_hash=schema_hash,
        prompt_version=prompt_version,
        producer_input_hash=input_fingerprint,
        accepted_typed_output_hash=content_hash(payload),
        validation_errors=[],
    )
    session.add(snapshot)
    session.flush()
    return attempt, snapshot


def _build_continuation_lineage(
    session,
    qualification_factory,
    monkeypatch,
) -> SimpleNamespace:
    recovery_now = datetime(2026, 8, 12, 15, tzinfo=timezone.utc)
    historical = _expired_zero_effect_source(
        session,
        qualification_factory,
        recovery_now=recovery_now,
        name="Controlled production continuation",
    )
    _ready_authorities(monkeypatch)
    _patch_current_authorities(monkeypatch)
    root_lineage = ScriptContractReplacementAuthorityService(
        session, now=lambda: recovery_now
    ).create_operator_first_video_recovery(
        historical_candidate_id=historical.candidate.id,
        actor=_controlled_actor(),
    )
    root = root_lineage.qualification
    original_payload = _v2_payload(root)
    draft = ScriptQualificationService(session).accept_writer_output(
        root, original_payload
    )
    structural = ScriptQualificationService(session)._structural_receipt(root, draft)
    assert structural["status"] == "PASS"
    root.result_receipts = {
        "structural": structural,
        "grounding": {
            "status": "BLOCK",
            "reason_codes": ["SCRIPT_CLAIM_PARTIALLY_SUPPORTED"],
        },
    }
    verifier_payload = {
        "material_claim_inventory": [
            {
                "observed_claim_id": "root-partial-claim",
                "span": {
                    "text": original_payload["sections"][0]["narration"],
                    "section_id": original_payload["sections"][0]["section_id"],
                },
                "claim_type": "FACTUAL_ASSERTION",
                "materiality_state": "MATERIAL",
                "writer_declared_claim_id": "claim-001",
                "factual_evidence_span_ids": [],
                "semantic_relation": "PARTIALLY_SUPPORTED",
                "assignment_requirement_ids": [],
                "reason_codes": ["SCRIPT_CLAIM_PARTIALLY_SUPPORTED"],
            }
        ]
    }
    _completed_attempt_with_snapshot(
        session,
        run=root,
        phase="VERIFIER",
        payload=verifier_payload,
        provider_outcome="SCRIPT_QUALIFICATION_BLOCKED",
        prompt_version=root.verifier_prompt_version,
        identity="root-verifier",
    )
    root.state = "BLOCKED_NON_REPAIRABLE"
    root.failure_receipt = {"reason_codes": ["SCRIPT_CLAIM_PARTIALLY_SUPPORTED"]}
    ScriptQualificationRecoveryService(
        session, now=lambda: recovery_now
    ).settle_deterministic_block(root, reason_code="SCRIPT_QUALIFICATION_BLOCKED")
    repair_source = ScriptContentRepairService(
        session, now=lambda: recovery_now
    ).authorize(source_qualification_run_id=root.id)

    repaired_payload = _v2_payload(repair_source, repaired=True)
    source_attempt, source_snapshot = _completed_attempt_with_snapshot(
        session,
        run=repair_source,
        phase="WRITER",
        payload=repaired_payload,
        provider_outcome="SCRIPT_WRITER_OUTPUT_INVALID",
        prompt_version=repair_source.writer_prompt_version,
        identity="repair-writer",
    )
    repair_source.state = "BLOCKED_NON_REPAIRABLE"
    repair_source.script_payload = None
    repair_source.failure_receipt = {
        "reason_codes": ["SCRIPT_WRITER_OUTPUT_INVALID"],
        "detail": "SCRIPT_CONTENT_REPAIR_SECTION_IDENTITY_CHANGED",
    }
    continuation_now = repair_source.logical_deadline_at + timedelta(seconds=1)
    ScriptQualificationRecoveryService(
        session, now=lambda: continuation_now
    ).settle_deterministic_block(
        repair_source, reason_code="SCRIPT_WRITER_OUTPUT_INVALID"
    )
    source_slot = session.get(LongFormPublishSlot, repair_source.publish_slot_id)
    source_slot_snapshot = {
        "id": source_slot.id,
        "state": source_slot.state,
        "reserved_candidate_id": source_slot.reserved_candidate_id,
        "admitted_video_project_id": source_slot.admitted_video_project_id,
        "intended_publish_at": source_slot.intended_publish_at,
        "target_start_window_open_at": source_slot.target_start_window_open_at,
        "target_start_window_close_at": source_slot.target_start_window_close_at,
        "replacement_lineage_key": source_slot.replacement_lineage_key,
    }
    recovery = ScriptWriterOutputRecoveryService(
        session,
        now=lambda: continuation_now,
        provider=SimpleNamespace(),
    )
    child = recovery.continue_after_content_repair_scope_reclassification(
        source_qualification_run_id=repair_source.id
    )
    authority = session.scalar(
        select(ControlledProductionContinuationAuthority).where(
            ControlledProductionContinuationAuthority.continuation_qualification_run_id
            == child.id
        )
    )
    continuation_slot = session.get(LongFormPublishSlot, child.publish_slot_id)
    assert authority is not None and continuation_slot is not None
    return SimpleNamespace(
        historical=historical,
        root_lineage=root_lineage,
        root=root,
        source=repair_source,
        source_attempt=source_attempt,
        source_snapshot=source_snapshot,
        source_slot=source_slot,
        source_slot_snapshot=source_slot_snapshot,
        child=child,
        continuation_slot=continuation_slot,
        authority=authority,
        recovery=recovery,
        now=continuation_now,
    )


def _fresh_attempt(
    run: ScriptQualificationRun,
    *,
    phase: str,
    identity: str,
) -> ScriptQualificationBackgroundAttempt:
    return ScriptQualificationBackgroundAttempt(
        script_qualification_run_id=run.id,
        phase=phase,
        provider="OPENAI",
        model=run.model,
        lane=f"script_qualification.{phase.casefold()}",
        task=f"script_qualification.{phase.casefold()}",
        input_fingerprint=content_hash({"input": identity}),
        immutable_input_hashes={},
        client_correlation_id=f"{identity}:{uuid.uuid4()}",
        background_status="SUBMIT_PENDING",
        logical_deadline_at=run.logical_deadline_at,
        poll_count=0,
        submission_attempt_count=0,
        response_schema_identifier=f"schema-{identity}",
        response_schema_hash=content_hash({"schema": identity}),
        prompt_version=(
            run.writer_prompt_version
            if phase == "WRITER"
            else run.verifier_prompt_version
        ),
    )


def _forked_child(source: ScriptQualificationRun) -> ScriptQualificationRun:
    identity = uuid.uuid4().hex
    return ScriptQualificationRun(
        editorial_idea_candidate_id=source.editorial_idea_candidate_id,
        publish_slot_id=source.publish_slot_id,
        launch_run_id=source.launch_run_id,
        topic_definition_id=source.topic_definition_id,
        topic_definition_hash=source.topic_definition_hash,
        script_assignment=deepcopy(source.script_assignment),
        script_assignment_hash=source.script_assignment_hash,
        factual_evidence_pack=deepcopy(source.factual_evidence_pack),
        factual_evidence_pack_hash=source.factual_evidence_pack_hash,
        memory_digest=deepcopy(source.memory_digest),
        memory_digest_hash=source.memory_digest_hash,
        runtime_contract=deepcopy(source.runtime_contract),
        runtime_contract_hash=source.runtime_contract_hash,
        assignment_resolution=deepcopy(source.assignment_resolution),
        assignment_resolution_hash=source.assignment_resolution_hash,
        episode_reservation_active=False,
        writer_prompt_version=source.writer_prompt_version,
        verifier_prompt_version=source.verifier_prompt_version,
        gate_policy_version=source.gate_policy_version,
        model=source.model,
        logical_attempt_number=source.logical_attempt_number + 1,
        logical_identity_hash=content_hash({"fork": identity}),
        supersedes_qualification_run_id=source.id,
        recovery_key=f"focused-continuation-fork:{identity}",
        recovery_requested_at=source.recovery_requested_at,
        logical_deadline_at=source.logical_deadline_at,
        state="RESERVED",
        writer_attempt_key=f"fork-writer:{identity}",
        verifier_attempt_key=f"fork-verifier:{identity}",
        repair_attempts=1,
        script_contract_version=source.script_contract_version,
        replacement_authority_id=source.replacement_authority_id,
    )


def test_controlled_continuation_is_exact_idempotent_and_db_enforced(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    lineage = _build_continuation_lineage(
        db_session, qualification_factory, monkeypatch
    )
    authority = lineage.authority
    child = lineage.child
    slot = lineage.continuation_slot

    assert authority.schema_version == CONTROLLED_CONTINUATION_SCHEMA
    assert authority.continuation_reason == CONTROLLED_CONTINUATION_REASON
    assert authority.root_replacement_authority_id == lineage.root_lineage.authority.id
    assert authority.source_qualification_run_id == lineage.source.id
    assert authority.source_slot_id == lineage.source_slot.id
    assert authority.continuation_candidate_id == child.editorial_idea_candidate_id
    assert authority.continuation_slot_id == slot.id == child.publish_slot_id
    assert authority.continuation_qualification_run_id == child.id
    assert authority.source_background_attempt_id == lineage.source_attempt.id
    assert authority.source_provider_response_snapshot_id == lineage.source_snapshot.id
    assert authority.max_writer_submissions == 0
    assert authority.max_verifier_submissions == 1
    assert authority.authority_hash == content_hash(
        controlled_continuation_authority_body(authority)
    )
    assert authority.slot_projection == {
        "slot_id": str(slot.id),
        "source_slot_id": str(lineage.source_slot.id),
        "launch_run_id": str(slot.launch_run_id),
        "launch_policy_version_id": str(slot.launch_policy_version_id),
        "company_id": str(slot.company_id),
        "channel_workspace_id": str(slot.channel_workspace_id),
        "local_publish_date": slot.local_publish_date.isoformat(),
        "intended_publish_at": slot.intended_publish_at.isoformat(),
        "target_start_window_open_at": slot.target_start_window_open_at.isoformat(),
        "target_start_window_close_at": slot.target_start_window_close_at.isoformat(),
        "reserved_candidate_id": str(slot.reserved_candidate_id),
        "replacement_authority_id": str(slot.replacement_authority_id),
        "replacement_reason": slot.replacement_reason,
        "replacement_lineage_key": slot.replacement_lineage_key,
    }
    assert slot.id != lineage.source_slot.id
    assert slot.replaces_slot_id == lineage.source_slot.id
    assert slot.state == "QUALIFICATION_RESERVED"
    assert slot.target_start_window_open_at == lineage.now
    assert child.logical_deadline_at == authority.qualification_deadline
    assert child.logical_deadline_at < authority.production_window_end
    assert child.state == "SCRIPT_GENERATED"
    assert child.writer_receipt["writer_submission_count_for_new_recovery"] == 0
    assert {
        "id": lineage.source_slot.id,
        "state": lineage.source_slot.state,
        "reserved_candidate_id": lineage.source_slot.reserved_candidate_id,
        "admitted_video_project_id": lineage.source_slot.admitted_video_project_id,
        "intended_publish_at": lineage.source_slot.intended_publish_at,
        "target_start_window_open_at": (
            lineage.source_slot.target_start_window_open_at
        ),
        "target_start_window_close_at": (
            lineage.source_slot.target_start_window_close_at
        ),
        "replacement_lineage_key": lineage.source_slot.replacement_lineage_key,
    } == lineage.source_slot_snapshot
    assert (
        db_session.scalar(
            select(func.count(ScriptQualificationBackgroundAttempt.id)).where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == child.id,
                ScriptQualificationBackgroundAttempt.phase == "WRITER",
            )
        )
        == 0
    )
    event = db_session.scalar(
        select(DomainEvent).where(
            DomainEvent.aggregate_id == child.id,
            DomainEvent.payload["recovery_mode"].astext
            == "BOUNDED_CONTENT_REPAIR_VERIFIER_CONTINUATION",
        )
    )
    assert event is not None
    assert event.metadata_["writer_submission_count_for_new_recovery"] == 0
    assert event.metadata_["verifier_submission_limit"] == 1

    repeated = lineage.recovery.continue_after_content_repair_scope_reclassification(
        source_qualification_run_id=lineage.source.id
    )
    assert repeated.id == child.id
    assert (
        db_session.scalar(
            select(func.count(ControlledProductionContinuationAuthority.id)).where(
                ControlledProductionContinuationAuthority.root_replacement_authority_id
                == lineage.root_lineage.authority.id
            )
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count(LongFormPublishSlot.id)).where(
                LongFormPublishSlot.replaces_slot_id == lineage.source_slot.id,
                LongFormPublishSlot.replacement_reason
                == CONTROLLED_CONTINUATION_REASON,
            )
        )
        == 1
    )

    assert (
        resolve_replacement_qualification_leaf(
            db_session, authority=lineage.root_lineage.authority
        ).id
        == child.id
    )
    guard = ScriptQualificationBackgroundService(
        db_session,
        now=lambda: lineage.now,
        provider=SimpleNamespace(),
    )
    assert guard._recovery_guards_pass(child, phase="VERIFIER") is True
    verifier_context = guard._verifier_context(child)
    assert verifier_context["controlled_continuation_authority"] == {
        "schema_version": authority.schema_version,
        "continuation_authority_id": str(authority.id),
        "authority_hash": authority.authority_hash,
        "continuation_reason": authority.continuation_reason,
        "max_writer_submissions": 0,
        "max_verifier_submissions": 1,
        "qualification_deadline": authority.qualification_deadline.isoformat(),
    }

    for statement in (
        update(ControlledProductionContinuationAuthority)
        .where(ControlledProductionContinuationAuthority.id == authority.id)
        .values(max_verifier_submissions=0),
        delete(ControlledProductionContinuationAuthority).where(
            ControlledProductionContinuationAuthority.id == authority.id
        ),
    ):
        with pytest.raises(
            ProgrammingError,
            match="controlled production continuation authorities are immutable",
        ):
            with db_session.begin_nested():
                db_session.execute(statement)
                db_session.flush()
        db_session.expire_all()
    assert db_session.get(ControlledProductionContinuationAuthority, authority.id)

    with pytest.raises(
        ProgrammingError,
        match="controlled continuation forbids writer submissions",
    ):
        with db_session.begin_nested():
            db_session.add(
                _fresh_attempt(child, phase="WRITER", identity="forbidden-writer")
            )
            db_session.flush()
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count(ScriptQualificationBackgroundAttempt.id)).where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == child.id,
                ScriptQualificationBackgroundAttempt.phase == "WRITER",
            )
        )
        == 0
    )

    verifier_attempt = _fresh_attempt(
        child, phase="VERIFIER", identity="authorized-verifier"
    )
    db_session.add(verifier_attempt)
    db_session.flush()
    assert verifier_attempt.id is not None

    with pytest.raises(
        ValidationFailureError,
        match="SCOPED_REPLACEMENT_QUALIFICATION_LINEAGE_DRIFT",
    ):
        with db_session.begin_nested():
            db_session.execute(
                update(ScriptQualificationRun)
                .where(ScriptQualificationRun.id == child.id)
                .values(publish_slot_id=lineage.source_slot.id)
            )
            db_session.expire_all()
            resolve_replacement_qualification_leaf(
                db_session, authority=lineage.root_lineage.authority
            )
    db_session.expire_all()

    with pytest.raises(
        ValidationFailureError,
        match="SCOPED_REPLACEMENT_QUALIFICATION_LINEAGE_FORK",
    ):
        with db_session.begin_nested():
            db_session.add(_forked_child(lineage.source))
            db_session.flush()
            resolve_replacement_qualification_leaf(
                db_session, authority=lineage.root_lineage.authority
            )


def test_runtime_migration_guard_requires_controlled_continuation_revision(
    db_session,
) -> None:
    status = RuntimeMigrationGuard(db_session).inspect()

    assert status.ready is True
    assert status.current_revision == REQUIRED_RUNTIME_DB_REVISION
    assert status.required_revision == "0076_v2_timing_recovery"

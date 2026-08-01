from __future__ import annotations

import runpy
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.geo_market import DestinationBinding
from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.production_package import ProductionDurationContractV2
from app.contracts.workflow import ArtifactCreate
from app.core.errors import ValidationFailureError
from app.db.models.m10_2 import MediaRenderRoutingDecision
from app.db.models.m5 import IdeaMarketPreflight
from app.db.models.workflow import Artifact, ArtifactVersion
from app.services.production_package import semantic_hash
from app.services.v2_support_authority import (
    LLMRouterV2SupportProducer,
    V2FrozenSourceRef,
    V2FrozenSupportEnvelope,
    V2GeneratedCitation,
    V2GeneratedClaim,
    V2GeneratedSection,
    V2ProducerReceipt,
    V2SupportAuthorityPrepareCommand,
    V2SupportAuthorityService,
    V2SupportProductionContext,
    V2TrustedSupportDraft,
)
from app.services.workflow import ArtifactService


ROOT = Path(__file__).resolve().parents[1]


def _phase3_scope(session: Session) -> Any:
    module = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    return module["_scope"](session)


def _phase4_script_scope(session: Session) -> Any:
    base = _phase3_scope(session)
    module = runpy.run_path(str(ROOT / "tests/test_phase4_support_compiler.py"))
    return module["_new_long_scope_with_approved_script"](session, base)


def _configure_verified_destination(scope: Any) -> None:
    binding = DestinationBinding(
        binding_version=1,
        channel_id=scope.channel.id,
        channel_key=scope.channel.key,
        platform="YOUTUBE",
        platform_account_ref="youtube-account://v2-support-local",
        platform_channel_id="UC_V2_SUPPORT_LOCAL",
        channel_handle="@v2-support-local",
        target_market_profile_ref="target-market-profile://v2-support/v1",
        target_market_profile_hash="d" * 64,
        target_market="US",
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status="VERIFIED",
        credential_ref="credential://v2-support/local",
        verification_state="VERIFIED",
        verification_timestamp="2026-07-29T00:00:00+00:00",
        approval_ref="operator-approval://v2-support/destination",
    ).model_dump(mode="json")
    scope.channel.metadata_ = {
        **(scope.channel.metadata_ or {}),
        "destination_governance": {
            "active_binding_ref": (f"destination-binding://{scope.channel.key}/v1"),
            "bindings": [binding],
        },
    }


class _FakeTrustedProducer:
    def __init__(
        self,
        *,
        invalid_citation: bool = False,
        duplicate_claims: bool = False,
    ):
        self.calls = 0
        self.invalid_citation = invalid_citation
        self.duplicate_claims = duplicate_claims

    def produce(self, context: V2SupportProductionContext) -> V2TrustedSupportDraft:
        self.calls += 1
        target_words = round(
            context.duration_contract.target_duration_ms * 150 / 60_000
        )
        sentences: list[str] = []
        while len(" ".join(sentences).split()) < target_words:
            index = len(sentences) + 1
            sentences.append(
                " ".join(
                    [
                        f"Verified insight {index}",
                        "explains the approved operating context through",
                        "a concrete local workflow measurable review checkpoints",
                        "and source bound decisions for the production team.",
                    ]
                )
            )
        third = max(1, len(sentences) // 3)
        groups = [
            sentences[:third],
            sentences[third : third * 2],
            sentences[third * 2 :],
        ]
        sections = [
            V2GeneratedSection(
                section_id=f"section-{index}",
                heading=f"Approved section {index}",
                narration=" ".join(group),
            )
            for index, group in enumerate(groups, start=1)
            if group
        ]
        script = " ".join(section.narration for section in sections)
        source = context.frozen_sources[0]
        excerpt = (
            "Caller supplied approved script must never be trusted."
            if self.invalid_citation
            else source.fact_statements[0]
        )
        claims = [
            V2GeneratedClaim(
                claim_id=f"claim-{index + 1:03d}",
                claim_text=sentences[0 if self.duplicate_claims else index],
                citations=[
                    V2GeneratedCitation(
                        source_ref_id=source.id,
                        source_excerpt=excerpt,
                    )
                ],
            )
            for index in range(3)
        ]
        output_payload = {
            "approved_script_text": script,
            "language": context.expected_language,
            "sections": [section.model_dump(mode="json") for section in sections],
            "claims": [claim.model_dump(mode="json") for claim in claims],
        }
        return V2TrustedSupportDraft(
            approved_script_text=script,
            language=context.expected_language,
            sections=sections,
            claims=claims,
            producer_receipt=V2ProducerReceipt(
                producer_type="LLM_ROUTER",
                producer_version="test-trusted-producer.v1",
                lane_name="long_context_text",
                selected_model="injected-no-provider",
                fallback_level="PRIMARY",
                route_attempt_id=uuid.uuid4(),
                producer_input_hash=semantic_hash(context.model_dump(mode="json")),
                producer_output_hash=semantic_hash(output_payload),
            ),
        )


def _command(
    scope: Any,
    *,
    idempotency_key: str = "support-authority-1",
    max_budget_usd: str = "25.00",
):
    assert scope.admission.editorial_calendar_slot_id is not None
    return V2SupportAuthorityPrepareCommand(
        video_project_id=scope.project.id,
        source_type="LONG_FORM_PLAN",
        source_id=scope.admission.editorial_calendar_slot_id,
        actor_user_id=scope.operator.id,
        idempotency_key=idempotency_key,
        max_budget_usd=max_budget_usd,
    )


def test_seals_domain_envelope_and_replays_without_provider_or_route_rows(
    db_session: Session,
) -> None:
    scope = _phase4_script_scope(db_session)
    _configure_verified_destination(scope)
    preflight = db_session.get(
        IdeaMarketPreflight,
        scope.admission.idea_market_preflight_id,
    )
    assert preflight is not None
    assert "approved_script" in preflight.evidence_blob

    producer = _FakeTrustedProducer()
    service = V2SupportAuthorityService(db_session, producer=producer)
    first = service.prepare(_command(scope))
    second = service.prepare(_command(scope))

    assert first.replayed is False
    assert second.replayed is True
    assert second.artifact_version_id == first.artifact_version_id
    assert second.envelope_hash == first.envelope_hash
    assert producer.calls == 1
    assert db_session.scalar(select(func.count(MediaRenderRoutingDecision.id))) == 0

    artifact = db_session.get(Artifact, first.artifact_id)
    version = db_session.get(ArtifactVersion, first.artifact_version_id)
    assert artifact is not None
    assert version is not None
    envelope = V2FrozenSupportEnvelope.model_validate(version.content)
    assert artifact.artifact_type == "v2_frozen_support_envelope"
    assert artifact.status == "approved"
    assert version.status == "approved"
    assert (
        version.packaging_metadata["_vcos_domain_authority"]["writer"]
        == "server_domain_service"
    )
    caller_script = str(preflight.evidence_blob["approved_script"])
    assert caller_script != envelope.approved_script.approved_script_text
    assert all(
        caller_script not in statement
        for source in envelope.frozen_sources
        for statement in source.fact_statements
    )
    assert len(envelope.claim_source_bindings) == 3
    assert len(envelope.approved_script.sections) == 3
    assert len({binding.claim_text for binding in envelope.claim_source_bindings}) == 3
    assert envelope.local_generated_card_rights.external_asset_refs == []
    assert envelope.local_generated_card_rights.stock_asset_refs == []
    assert len(envelope.native_routes) == 4
    assert all(route.routing_decision_id is None for route in envelope.native_routes)
    assert all(route.paid_provider_call is False for route in envelope.native_routes)
    assert all(route.max_cost_usd == 0 for route in envelope.native_routes)
    assert {route.stage for route in envelope.native_routes} == {
        "MEDIA",
        "RENDER",
        "QC",
        "ARCHIVE",
    }
    assert all(
        route.capability_entry_id is not None
        for route in envelope.native_routes
        if route.stage != "ARCHIVE"
    )
    assert envelope.zero_cost_budget.authorized_cost_usd == 0
    assert envelope.zero_cost_budget.paid_provider_calls_allowed is False
    assert envelope.verified_destination.binding.verification_state == "VERIFIED"


def test_rejects_idempotency_and_frozen_source_drift_without_regeneration(
    db_session: Session,
) -> None:
    scope = _phase3_scope(db_session)
    _configure_verified_destination(scope)
    producer = _FakeTrustedProducer()
    service = V2SupportAuthorityService(db_session, producer=producer)
    service.prepare(_command(scope))
    assert producer.calls == 1

    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_ENVELOPE_IMMUTABLE_DRIFT",
    ):
        service.prepare(_command(scope, idempotency_key="different-key"))
    assert producer.calls == 1

    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_ENVELOPE_IMMUTABLE_DRIFT",
    ):
        service.prepare(_command(scope, max_budget_usd="26.00"))
    assert producer.calls == 1


def test_rejects_unbound_claim_and_public_domain_artifact_write(
    db_session: Session,
) -> None:
    scope = _phase3_scope(db_session)
    _configure_verified_destination(scope)
    producer = _FakeTrustedProducer(invalid_citation=True)
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_CLAIM_SOURCE_BINDING_INVALID",
    ):
        V2SupportAuthorityService(
            db_session,
            producer=producer,
        ).prepare(_command(scope))

    duplicate_claims = _FakeTrustedProducer(duplicate_claims=True)
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_CLAIM_SOURCE_BINDING_INVALID",
    ):
        V2SupportAuthorityService(
            db_session,
            producer=duplicate_claims,
        ).prepare(_command(scope))

    with pytest.raises(
        ValidationFailureError,
        match="AUTHORITY_ARTIFACT_DOMAIN_SERVICE_REQUIRED",
    ):
        ArtifactService(db_session).create_artifact(
            data=ArtifactCreate(
                video_project_id=scope.project.id,
                artifact_type="v2_frozen_support_envelope",
                status="approved",
                created_by_user_id=scope.operator.id,
            )
        )


class _FakeRouter:
    def __init__(self, response: LLMRouteResponse):
        self.response = response
        self.calls = 0

    def route(self, **_: Any) -> LLMRouteResponse:
        self.calls += 1
        return self.response


def _producer_context(scope: Any) -> V2SupportProductionContext:
    duration = ProductionDurationContractV2.model_validate(
        scope.duration.model_dump(mode="json")
    )
    source = V2FrozenSourceRef(
        type="editorial_calendar_slot",
        source_kind="FROZEN_EDITORIAL_SLOT",
        id=uuid.uuid4(),
        ref=f"editorial-calendar-slot://{uuid.uuid4()}",
        content_hash="a" * 64,
        fact_statements=["Production goal: explain the approved local workflow."],
    )
    return V2SupportProductionContext(
        video_project_id=scope.project.id,
        production_lane="LONG_FORM",
        title="Guarded producer",
        expected_language="en",
        duration_contract=duration,
        frozen_sources=[source],
    )


def test_llm_router_producer_blocks_disabled_and_invalid_without_real_call(
    db_session: Session,
) -> None:
    scope = _phase3_scope(db_session)
    with pytest.raises(ValidationError):
        V2SupportAuthorityPrepareCommand.model_validate(
            {
                "video_project_id": str(scope.project.id),
                "source_type": "LONG_FORM_PLAN",
                "source_id": str(scope.admission.editorial_calendar_slot_id),
                "actor_user_id": str(scope.operator.id),
                "idempotency_key": "caller-self-attestation",
                "approved_script_text": "Public callers cannot submit this.",
            }
        )
    context = _producer_context(scope)
    invalid_response = LLMRouteResponse(
        status="SUCCESS",
        lane_name="long_context_text",
        selected_model="injected-no-provider",
        fallback_level="PRIMARY",
        structured_output={"unexpected": True},
        route_attempt_id=uuid.uuid4(),
    )
    fake_router = _FakeRouter(invalid_response)
    disabled = LLMRouterV2SupportProducer(
        db_session,
        router=fake_router,
        enabled=False,
    )
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_LLM_PRODUCER_DISABLED",
    ):
        disabled.produce(context)
    assert fake_router.calls == 0

    enabled = LLMRouterV2SupportProducer(
        db_session,
        router=fake_router,
        enabled=True,
    )
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_LLM_PRODUCER_INVALID",
    ):
        enabled.produce(context)
    assert fake_router.calls == 1

    skipped_router = _FakeRouter(
        LLMRouteResponse(
            status="SKIPPED",
            lane_name="long_context_text",
            selected_model="injected-no-provider",
            fallback_level="PRIMARY",
            route_attempt_id=uuid.uuid4(),
            reason_codes=["OPENAI_REAL_EXECUTION_DISABLED"],
        )
    )
    guarded = LLMRouterV2SupportProducer(
        db_session,
        router=skipped_router,
        enabled=True,
    )
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_LLM_PRODUCER_DISABLED",
    ):
        guarded.produce(context)
    assert skipped_router.calls == 1


def test_envelope_model_rejects_route_budget_rebinding(
    db_session: Session,
) -> None:
    scope = _phase3_scope(db_session)
    _configure_verified_destination(scope)
    result = V2SupportAuthorityService(
        db_session,
        producer=_FakeTrustedProducer(),
    ).prepare(_command(scope))
    version = db_session.get(ArtifactVersion, result.artifact_version_id)
    assert version is not None
    tampered = dict(version.content)
    budget = dict(tampered["zero_cost_budget"])
    budget["route_hashes"] = ["f" * 64] * 4
    tampered["zero_cost_budget"] = budget
    with pytest.raises(ValidationError):
        V2FrozenSupportEnvelope.model_validate(tampered)

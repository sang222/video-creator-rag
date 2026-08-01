from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.contracts.nich1 import (
    NICHE_GATE_STRICT_ORDER,
    MetadataNicheAlignmentInput,
    NicheCriterionEvidence,
    NicheDossierScope,
    NicheEvidenceRef,
    NicheGateKey,
    NicheGateVerdict,
    NicheReasonCode,
    ScriptNicheAlignmentInput,
    ThumbnailNicheAlignmentInput,
    TopicNicheAlignmentInput,
    VisualNicheAlignmentInput,
    nich1_stable_hash,
)
from app.services.nich1 import (
    EditorialSlotValidator,
    MetadataNicheAlignmentGate,
    NicheAlignmentDossierBuilder,
    NicheAlignmentGateRegistry,
    NicheContractCompilationError,
    NicheContractDigestCompiler,
    ScriptNicheAlignmentGate,
    ThumbnailNicheAlignmentGate,
    TopicNicheAlignmentGate,
    VisualNicheAlignmentGate,
    channel_fit_threshold_from_compiled_policy,
    evaluate_channel_fit,
)
from app.services.r3d3 import (
    AgentContextContractRegistry,
    PromptBudgetGate,
    build_script_contract_digest,
)
from app.db.models import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    EditorialCalendarSlot,
    RetrievalPlanSnapshot,
)
from app.contracts.m5 import ContextPackSnapshotCreate
from app.core.errors import ValidationFailureError
from app.services.m5 import (
    ResourceResolverService,
    _hash_payload,
    _validate_nich1_editorial_context_authority,
)
from app.services.m12_2 import (
    FULL_REHEARSAL_AGENT_CHAIN,
    PACKAGE_AGENT_CHAIN,
    M122NicheGateEvaluator,
    PackageAgentStep,
)
from app.services.r3d4 import NicheAlignmentEvidenceGate


def _authority() -> SimpleNamespace:
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    category_id = uuid.uuid4()
    slot_id = uuid.uuid4()
    profile_input = {
        "template_key": "small_team_ai",
        "series_plan": [
            {
                "key": "workflow-audit",
                "content_pillar_key": "Practical AI workflows",
            }
        ],
        "media_style": {"niche_visual_source_profile": "STOCK_ASSISTED"},
        "voice_style": {"tone": "calm practical", "pacing": "measured"},
    }
    profile = SimpleNamespace(
        id=profile_id,
        channel_workspace_id=channel_id,
        status="active",
        profile_input=profile_input,
        profile_input_hash=nich1_stable_hash(profile_input),
    )
    contract = {
        "contract_status": "COMPLETE",
        "channel_identity": {
            "channel_key": "small-team-ai",
            "niche": "Practical AI for small professional teams",
            "positioning": "Evidence-aware AI operations for lean teams",
            "brand_promise": "Turn repeated work into bounded, auditable workflows",
            "series_plan": profile_input["series_plan"],
        },
        "target_audience": {
            "primary_persona": "small-team operators",
            "audience_segments": ["founders", "operations leads"],
            "pain_points": ["repetitive support work", "unclear automation risk"],
            "desired_outcomes": [
                "save operator time responsibly",
                "adopt auditable workflows",
            ],
        },
        "market_locale": {
            "primary_market": "US",
            "content_language": "en",
            "audience_locale": "en-US",
        },
        "editorial_strategy": {
            "content_pillars": [
                "Practical AI workflows",
                "Small-team operating leverage",
            ],
            "allowed_topics": ["AI workflow", "small-team operations"],
            "forbidden_topics": ["crypto trading", "medical guarantees"],
        },
        "voice_style": {
            "narration_tone": "calm practical documentary",
            "pacing": "measured",
            "allowed_style": ["evidence-aware"],
            "forbidden_style": ["hype"],
        },
        "format_policy": {
            "primary_format": "long-form documentary/explainer",
            "target_runtime_minutes": {"minimum": 6, "maximum": 12},
        },
        "media_policy": {"niche_visual_source_profile": "STOCK_ASSISTED"},
    }
    compiled_payload = {
        "channel_contract_json": contract,
        "contract_status": "COMPLETE",
        "channel_scoped_policy": {
            "channel_visual_strategy_profile": {
                "niche_visual_source_profile": "STOCK_ASSISTED"
            },
            "gate_policy": {
                "niche_alignment_required": True,
                "channel_fit_threshold": 0.80,
            },
            "budget_policy": {"max_estimated_cost_per_video": 1.0},
        },
    }
    snapshot = SimpleNamespace(
        id=snapshot_id,
        channel_workspace_id=channel_id,
        channel_profile_version_id=profile_id,
        status="active",
        compiled_payload=compiled_payload,
        content_hash=nich1_stable_hash(compiled_payload),
    )
    channel = SimpleNamespace(
        id=channel_id,
        company_id=company_id,
        key="small-team-ai",
        active_policy_snapshot_id=snapshot_id,
    )
    category_payload = {
        "id": str(category_id),
        "name": "Workflow audits",
        "sub_niche": "AI workflow audits for lean operations",
    }
    category = SimpleNamespace(
        id=category_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        category_key="workflow-audits",
        name="Workflow audits",
        sub_niche="AI workflow audits for lean operations",
        audience_segment="small-team operators",
        content_pillar="Practical AI workflows",
        allowed_topics_json=["workflow audit", "support automation"],
        forbidden_topics_json=["enterprise ERP migration"],
        default_format_policy_json={"format": "explainer"},
        default_visual_style_json={"niche_visual_source_profile": "STOCK_ASSISTED"},
        status="ACTIVE",
        content_hash=nich1_stable_hash(category_payload),
    )
    slot = SimpleNamespace(
        id=slot_id,
        company_id=company_id,
        channel_workspace_id=channel_id,
        policy_snapshot_id=snapshot_id,
        category_id=category_id,
        content_pillar_id=None,
        content_pillar_key="Practical AI workflows",
        content_pillar="Practical AI workflows",
        series_key="workflow-audit",
        production_goal="Teach a small team to audit one support workflow",
        format_hint="long-form documentary/explainer",
        operational_envelope={},
    )
    return SimpleNamespace(
        channel=channel,
        profile=profile,
        snapshot=snapshot,
        category=category,
        slot=slot,
        contract=contract,
    )


def _digest(scope: SimpleNamespace):
    return NicheContractDigestCompiler().compile(
        channel=scope.channel,
        profile_version=scope.profile,
        policy_snapshot=scope.snapshot,
        category=scope.category,
        editorial_slot=scope.slot,
    )


def _ref(label: str = "semantic") -> NicheEvidenceRef:
    return NicheEvidenceRef(type="offline_test", ref=f"fixture://nich1/{label}")


def _semantic(gate) -> list[NicheCriterionEvidence]:
    return [
        NicheCriterionEvidence(
            criterion=criterion,
            verdict="PASS",
            score=0.92,
            rationale=f"Offline fixture supports {criterion.value}.",
            evidence_refs=[_ref(criterion.value.casefold())],
        )
        for criterion in sorted(gate.required_criteria, key=lambda item: item.value)
    ]


def _bindings(scope: SimpleNamespace):
    result = EditorialSlotValidator().validate(
        channel=scope.channel,
        profile_version=scope.profile,
        policy_snapshot=scope.snapshot,
        channel_contract=scope.contract,
        category=scope.category,
        editorial_slot=scope.slot,
        strict_production=True,
    )
    assert result.verdict == NicheGateVerdict.PASS
    assert result.slot_binding is not None
    assert result.category_binding is not None
    return result.slot_binding, result.category_binding


def _base_input(scope: SimpleNamespace, digest, subject: str) -> dict:
    return {
        "niche_contract_digest": digest,
        "niche_contract_digest_ref": "context-pack://editorial-pack#niche_contract_digest",
        "niche_contract_digest_hash": digest.content_hash,
        "active_policy_snapshot_ref": digest.compiled_policy_snapshot_ref,
        "active_policy_snapshot_hash": digest.compiled_policy_snapshot_hash,
        "subject_ref": f"artifact-version://{subject}",
        "subject_hash": nich1_stable_hash({"subject": subject}),
        "evidence_refs": [_ref(subject)],
    }


def _positive_gate_results(scope: SimpleNamespace, digest):
    slot_binding, category_binding = _bindings(scope)
    topic_gate = TopicNicheAlignmentGate()
    topic_input = TopicNicheAlignmentInput(
        **_base_input(scope, digest, "editorial-candidate"),
        channel_id=scope.channel.id,
        slot_binding=slot_binding,
        category_binding=category_binding,
        topic="How small teams audit an AI support workflow",
        angle="A bounded checklist for repetitive support work",
        claim_scope=["illustrative workflow steps"],
        semantic_evidence=_semantic(topic_gate),
    )
    topic_result = topic_gate.evaluate(topic_input)

    script_gate = ScriptNicheAlignmentGate()
    script_input = ScriptNicheAlignmentInput(
        **_base_input(scope, digest, "script"),
        editorial_idea_candidate_ref=topic_result.subject_ref,
        editorial_idea_candidate_hash=topic_input.subject_hash,
        topic_gate_ref=f"niche-gate://topic/{topic_result.content_hash}",
        topic_gate_result=topic_result,
        approved_topic=topic_input.topic,
        script_topic="How small teams audit an AI support workflow",
        script_text=(
            "A small team maps repetitive support work, verifies evidence, and "
            "adopts an auditable workflow without promising guaranteed savings."
        ),
        declared_primary_niche=digest.primary_niche,
        declared_sub_niche=digest.category_sub_niche,
        declared_category_id=digest.category_id,
        declared_content_pillar_key=digest.content_pillar_key,
        addressed_audience_pain_points=["repetitive support work"],
        addressed_audience_desired_outcomes=["adopt auditable workflows"],
        claim_scope=["illustrative workflow steps"],
        semantic_evidence=_semantic(script_gate),
    )
    script_result = script_gate.evaluate(script_input)

    visual_gate = VisualNicheAlignmentGate()
    visual_input = VisualNicheAlignmentInput(
        **_base_input(scope, digest, "visual-plan"),
        visual_direction_contract={
            "channel_id": str(scope.channel.id),
            "content_hash": nich1_stable_hash({"direction": "small-team-ai"}),
        },
        scene_visual_intents=[
            {
                "scene_id": "mechanism-1",
                "scene_class": "mechanism",
                "narrative_function": "explain workflow",
                "scene_meaning": "Show the audit workflow nodes",
                "semantic_intent": "Explain how it works",
                "niche_visual_source_profile": "STOCK_ASSISTED",
                "evidence_truth_requirement": 0.0,
            }
        ],
        visual_source_decisions=[
            {
                "scene_id": "mechanism-1",
                "preferred_source_route": "NATIVE_DIAGRAM",
                "niche_visual_source_profile": "STOCK_ASSISTED",
            }
        ],
        content_pillar_key=digest.content_pillar_key,
        category_id=digest.category_id,
        semantic_evidence=_semantic(visual_gate),
    )
    visual_result = visual_gate.evaluate(visual_input)

    thumbnail_gate = ThumbnailNicheAlignmentGate()
    thumbnail_input = ThumbnailNicheAlignmentInput(
        **_base_input(scope, digest, "thumbnail"),
        approved_topic=topic_input.topic,
        thumbnail_promise="Audit an AI support workflow for a small team",
        implied_niche=digest.primary_niche,
        visual_language="calm workflow diagram with one supporting office detail",
        semantic_evidence=_semantic(thumbnail_gate),
    )
    thumbnail_result = thumbnail_gate.evaluate(thumbnail_input)

    metadata_gate = MetadataNicheAlignmentGate()
    metadata_input = MetadataNicheAlignmentInput(
        **_base_input(scope, digest, "metadata"),
        approved_topic=topic_input.topic,
        title="How small teams audit an AI support workflow",
        description="A bounded workflow audit for lean support operations.",
        keywords=["AI workflow", "small team"],
        tags=["workflow audit"],
        chapters=["Map the repeated work", "Audit the handoff"],
        cta="Review one repeated workflow with your team.",
        declared_category_id=digest.category_id,
        declared_content_pillar_key=digest.content_pillar_key,
        semantic_evidence=_semantic(metadata_gate),
    )
    metadata_result = metadata_gate.evaluate(metadata_input)
    return SimpleNamespace(
        topic_input=topic_input,
        topic=topic_result,
        script_input=script_input,
        script=script_result,
        visual_input=visual_input,
        visual=visual_result,
        thumbnail_input=thumbnail_input,
        thumbnail=thumbnail_result,
        metadata_input=metadata_input,
        metadata=metadata_result,
    )


def test_digest_is_authoritative_bounded_deterministic_and_hash_bound() -> None:
    scope = _authority()
    first = _digest(scope)
    second = _digest(scope)

    assert first == second
    assert first.primary_niche == "Practical AI for small professional teams"
    assert first.category_sub_niche == "AI workflow audits for lean operations"
    assert first.content_pillar_key == "Practical AI workflows"
    assert first.series_key == "workflow-audit"
    assert first.visual_source_profile == "STOCK_ASSISTED"
    assert first.content_hash == nich1_stable_hash(
        first.model_dump(mode="json", exclude={"content_hash"})
    )
    assert "compiled_payload" not in first.model_dump_json()

    tampered = first.model_dump(mode="json")
    tampered["brand_promise"] = "Guaranteed revenue"
    with pytest.raises(ValidationError, match="NICH1_CONTENT_HASH_MISMATCH"):
        type(first).model_validate(tampered)


def test_strict_editorial_context_recompiles_authority_and_rejects_self_hashed_forgery() -> (
    None
):
    scope = _authority()
    digest = _digest(scope)
    digest_ref = {
        "type": "niche_contract_digest",
        "ref": digest.editorial_slot_ref + "#niche_contract_digest",
        "content_hash": digest.content_hash,
    }
    pack_content = {
        "niche_contract_digest": digest.model_dump(mode="json"),
        "niche_contract_digest_ref": digest_ref,
        "runtime_guard_digest": {
            "compiled_policy_snapshot_id": str(scope.snapshot.id),
            "compiled_policy_snapshot_hash": scope.snapshot.content_hash,
            "provider_calls_allowed": False,
            "direct_provider_sdk_allowed": False,
        },
        "agent_context_pack": {
            "agent_key": "TopicIdeaScoringAgent",
            "digests": {
                "niche_contract_digest": digest.model_dump(mode="json"),
            },
        },
    }
    policy_refs = [
        {
            "type": "niche_contract_digest_authority",
            "compiled_policy_snapshot_id": str(scope.snapshot.id),
            "content_hash": digest.content_hash,
        }
    ]

    def _pack(content, refs):
        payload = {
            "input_refs": [],
            "policy_refs": refs,
            "evidence_refs": [],
            "metric_refs": [],
            "memory_refs": [],
            "pack_content": content,
        }
        return SimpleNamespace(
            policy_snapshot_id=scope.snapshot.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            editorial_calendar_slot_id=scope.slot.id,
            pack_hash=_hash_payload(payload),
            **payload,
        )

    class _Session:
        def get(self, model, identifier):
            values = {
                (ChannelWorkspace, scope.channel.id): scope.channel,
                (ChannelProfileVersion, scope.profile.id): scope.profile,
                (EditorialCalendarSlot, scope.slot.id): scope.slot,
                (ContentCategory, scope.category.id): scope.category,
            }
            return values.get((model, identifier))

    valid_pack = _pack(pack_content, policy_refs)
    assert (
        _validate_nich1_editorial_context_authority(
            _Session(),
            context_pack=valid_pack,
            snapshot=scope.snapshot,
        ).content_hash
        == digest.content_hash
    )

    forged_payload = digest.model_dump(mode="json", exclude={"content_hash"})
    forged_payload["primary_niche"] = "Consumer crypto speculation"
    forged_payload["content_hash"] = nich1_stable_hash(forged_payload)
    forged = type(digest).model_validate(forged_payload)
    forged_content = deepcopy(pack_content)
    forged_content["niche_contract_digest"] = forged.model_dump(mode="json")
    forged_content["niche_contract_digest_ref"] = {
        **digest_ref,
        "content_hash": forged.content_hash,
    }
    forged_content["agent_context_pack"]["digests"]["niche_contract_digest"] = (
        forged.model_dump(mode="json")
    )
    forged_refs = [{**policy_refs[0], "content_hash": forged.content_hash}]
    with pytest.raises(
        ValidationFailureError,
        match="NICH1_EDITORIAL_CONTEXT_DIGEST_BINDING_MISMATCH",
    ):
        _validate_nich1_editorial_context_authority(
            _Session(),
            context_pack=_pack(forged_content, forged_refs),
            snapshot=scope.snapshot,
        )


def test_digest_rejects_stale_authority_and_slot_validator_preserves_legacy_read() -> (
    None
):
    scope = _authority()
    scope.channel.active_policy_snapshot_id = uuid.uuid4()
    with pytest.raises(NicheContractCompilationError) as exc:
        _digest(scope)
    assert NicheReasonCode.POLICY_SNAPSHOT_NOT_ACTIVE in exc.value.reason_codes

    scope = _authority()
    legacy_slot = deepcopy(scope.slot)
    legacy_slot.series_key = None
    strict = EditorialSlotValidator().validate(
        channel=scope.channel,
        profile_version=scope.profile,
        policy_snapshot=scope.snapshot,
        channel_contract=scope.contract,
        category=scope.category,
        editorial_slot=legacy_slot,
        strict_production=True,
    )
    readable = EditorialSlotValidator().validate(
        channel=scope.channel,
        profile_version=scope.profile,
        policy_snapshot=scope.snapshot,
        channel_contract=scope.contract,
        category=scope.category,
        editorial_slot=legacy_slot,
        strict_production=False,
    )
    assert strict.verdict == NicheGateVerdict.BLOCK
    assert strict.production_eligible is False
    assert readable.verdict == NicheGateVerdict.REVIEW_REQUIRED
    assert readable.legacy_readable is True
    assert NicheReasonCode.LEGACY_SLOT_STRICT_BINDING_REQUIRED in readable.reason_codes


def test_all_five_registered_gates_pass_and_production_dossier_is_consolidated() -> (
    None
):
    scope = _authority()
    digest = _digest(scope)
    results = _positive_gate_results(scope, digest)
    gate_results = [
        results.topic,
        results.script,
        results.visual,
        results.thumbnail,
        results.metadata,
    ]
    assert all(result.verdict == NicheGateVerdict.PASS for result in gate_results)
    registry = NicheAlignmentGateRegistry()
    assert registry.registered_gate_keys == NICHE_GATE_STRICT_ORDER

    channel_fit = evaluate_channel_fit(
        score=0.91,
        compiled_policy=scope.snapshot,
        gate_results=[results.topic],
        evidence_refs=[_ref("channel-fit")],
        caller_policy_fit_state="BLOCK",
    )
    assert channel_fit.channel_fit_result == NicheGateVerdict.PASS
    assert channel_fit.policy_fit_state == NicheGateVerdict.PASS
    assert channel_fit.caller_policy_fit_state_ignored == "BLOCK"
    dossier = NicheAlignmentDossierBuilder().build(
        digest=digest,
        digest_ref="artifact-version://niche-digest-v1",
        gate_results=gate_results,
        channel_fit=channel_fit,
        dossier_scope=NicheDossierScope.PRODUCTION_PACKAGE,
    )
    assert dossier.overall_verdict == NicheGateVerdict.PASS
    assert dossier.missing_gate_keys == []
    assert dossier.content_hash == nich1_stable_hash(
        dossier.model_dump(mode="json", exclude={"content_hash"})
    )


def test_m12_agent_chains_follow_strict_niche_order_and_include_thumbnail() -> None:
    expected = [
        "ScriptWriterAgent",
        "VisualPlanningAgent",
        "ThumbnailBriefAgent",
        "PublishingMetadataAgent",
    ]
    for chain in (PACKAGE_AGENT_CHAIN, FULL_REHEARSAL_AGENT_CHAIN):
        keys = [step.agent_key for step in chain]
        assert [keys.index(key) for key in expected] == sorted(
            keys.index(key) for key in expected
        )


def test_m12_derives_typed_script_gate_and_r3d4_rejects_tampered_pass() -> None:
    scope = _authority()
    digest = _digest(scope)
    positive = _positive_gate_results(scope, digest)
    digest_ref = {
        "type": "niche_contract_digest",
        "ref": "context-pack://daily-pack#niche_contract_digest",
        "content_hash": digest.content_hash,
    }
    effective = SimpleNamespace(
        category_runtime_context_json={
            "niche_contract_digest": digest.model_dump(mode="json"),
            "niche_contract_digest_ref": digest_ref,
        },
        channel_workspace_id=digest.channel_id,
        content_category_id=digest.category_id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        channel_profile_version_id=scope.profile.id,
        channel_contract_hash=digest.channel_contract_hash,
        source_refs_json=[
            {
                "type": "compiled_channel_policy_snapshot",
                "id": str(scope.snapshot.id),
                "content_hash": scope.snapshot.content_hash,
            }
        ],
    )
    artifacts = {
        "niche_contract_digest": digest.model_dump(mode="json"),
        "niche_contract_digest_ref": digest_ref,
        "topic_niche_alignment_gate": positive.topic.model_dump(mode="json"),
        "niche_gate_results": {
            NicheGateKey.TOPIC.value: positive.topic.model_dump(mode="json")
        },
        "approved_editorial_candidate": {"topic": positive.topic_input.topic},
        "narration_script": {
            "sentences": [
                {
                    "text": (
                        "How small teams audit an AI support workflow. Evidence-aware "
                        "AI operations for lean teams turn repetitive support work into "
                        "bounded auditable workflows and help operators adopt auditable workflows."
                    )
                }
            ],
            "evidence_refs": [{"type": "offline_test", "id": "script"}],
        },
    }
    result = M122NicheGateEvaluator().evaluate_after_agent(
        package_id=uuid.uuid4(),
        step=PackageAgentStep(
            "ScriptWriterAgent",
            "long_context_text",
            "narration_script",
            "long_form_script",
        ),
        artifacts=artifacts,
        effective_context=effective,
    )
    assert result is not None and result.verdict == NicheGateVerdict.PASS
    adapter = NicheAlignmentEvidenceGate(NicheGateKey.SCRIPT.value)
    assert (
        adapter.run(artifacts=artifacts, effective_context=effective).status == "PASS"
    )

    tampered = result.model_dump(mode="json")
    tampered["checked_policy_snapshot_hash"] = "f" * 64
    artifacts["niche_gate_results"][NicheGateKey.SCRIPT.value] = tampered
    blocked = adapter.run(artifacts=artifacts, effective_context=effective)
    assert blocked.status == "BLOCK"
    assert "NICHE_GATE_TYPED_RESULT_INVALID" in blocked.fail_codes


def test_missing_stale_forbidden_and_adjacent_topic_inputs_block() -> None:
    scope = _authority()
    digest = _digest(scope)
    results = _positive_gate_results(scope, digest)
    gate = TopicNicheAlignmentGate()
    base = results.topic_input.model_dump(mode="python")

    missing = gate.evaluate(
        TopicNicheAlignmentInput.model_validate(
            {
                **base,
                "niche_contract_digest": None,
                "niche_contract_digest_ref": None,
                "niche_contract_digest_hash": None,
            }
        )
    )
    stale = gate.evaluate(
        TopicNicheAlignmentInput.model_validate(
            {**base, "active_policy_snapshot_hash": "f" * 64}
        )
    )
    forbidden = gate.evaluate(
        TopicNicheAlignmentInput.model_validate(
            {**base, "topic": "Crypto trading automation for guaranteed gains"}
        )
    )
    adjacent = gate.evaluate(
        TopicNicheAlignmentInput.model_validate(
            {**base, "adjacent_niche_conflict": True}
        )
    )
    assert missing.verdict == NicheGateVerdict.BLOCK
    assert NicheReasonCode.NICHE_CONTRACT_DIGEST_MISSING in missing.reason_codes
    assert NicheReasonCode.NICHE_CONTRACT_DIGEST_STALE in stale.reason_codes
    assert NicheReasonCode.FORBIDDEN_TOPIC_CONFLICT in forbidden.reason_codes
    assert NicheReasonCode.ADJACENT_NICHE_CONFLICT in adjacent.reason_codes


def test_channel_fit_is_policy_derived_and_caller_pass_cannot_override_low_score() -> (
    None
):
    scope = _authority()
    digest = _digest(scope)
    topic = _positive_gate_results(scope, digest).topic
    assert channel_fit_threshold_from_compiled_policy(scope.snapshot) == 0.80
    result = evaluate_channel_fit(
        score=0.41,
        compiled_policy=scope.snapshot,
        gate_results=[topic],
        evidence_refs=[_ref("low-channel-fit")],
        caller_policy_fit_state="PASS",
    )
    assert result.channel_fit_result == NicheGateVerdict.BLOCK
    assert result.policy_fit_state == NicheGateVerdict.BLOCK
    assert NicheReasonCode.CHANNEL_FIT_BELOW_THRESHOLD in result.reason_codes
    assert NicheReasonCode.CALLER_POLICY_FIT_STATE_IGNORED in result.reason_codes
    with pytest.raises(ValueError, match="OUT_OF_RANGE"):
        evaluate_channel_fit(
            score=1.01,
            compiled_policy=scope.snapshot,
            gate_results=[topic],
            evidence_refs=[_ref("invalid")],
        )


def test_negative_downstream_drift_and_missing_package_gate_evidence_block() -> None:
    scope = _authority()
    digest = _digest(scope)
    positive = _positive_gate_results(scope, digest)

    script_data = positive.script_input.model_dump(mode="python")
    script_data.update(
        {
            "script_topic": "Enterprise ERP migration procurement",
            "adjacent_niche_conflict": True,
        }
    )
    script = ScriptNicheAlignmentGate().evaluate(
        ScriptNicheAlignmentInput.model_validate(script_data)
    )
    assert script.verdict == NicheGateVerdict.BLOCK
    assert NicheReasonCode.APPROVED_TOPIC_DRIFT in script.reason_codes

    visual_data = positive.visual_input.model_dump(mode="python")
    visual_data["visual_source_decisions"] = [
        {
            "scene_id": "mechanism-1",
            "preferred_source_route": "PEXELS_VIDEO",
            "niche_visual_source_profile": "STOCK_ASSISTED",
        }
    ]
    visual = VisualNicheAlignmentGate().evaluate(
        VisualNicheAlignmentInput.model_validate(visual_data)
    )
    assert visual.verdict == NicheGateVerdict.BLOCK
    assert (
        NicheReasonCode.MECHANISM_MEANING_REPLACED_BY_GENERIC_STOCK
        in visual.reason_codes
    )

    thumbnail_data = positive.thumbnail_input.model_dump(mode="python")
    thumbnail_data["thumbnail_promise"] = "The best crypto portfolio in 2026"
    thumbnail = ThumbnailNicheAlignmentGate().evaluate(
        ThumbnailNicheAlignmentInput.model_validate(thumbnail_data)
    )
    assert thumbnail.verdict == NicheGateVerdict.BLOCK
    assert NicheReasonCode.THUMBNAIL_TOPIC_PROMISE_MISMATCH in thumbnail.reason_codes

    metadata_data = positive.metadata_input.model_dump(mode="python")
    metadata_data["adjacent_niche_conflict"] = True
    metadata = MetadataNicheAlignmentGate().evaluate(
        MetadataNicheAlignmentInput.model_validate(metadata_data)
    )
    assert metadata.verdict == NicheGateVerdict.BLOCK
    assert NicheReasonCode.ADJACENT_NICHE_CONFLICT in metadata.reason_codes

    channel_fit = evaluate_channel_fit(
        score=0.90,
        compiled_policy=scope.snapshot,
        gate_results=[positive.topic],
        evidence_refs=[_ref("channel-fit")],
    )
    dossier = NicheAlignmentDossierBuilder().build(
        digest=digest,
        digest_ref="artifact-version://niche-digest-v1",
        gate_results=[positive.topic, positive.script],
        channel_fit=channel_fit,
        dossier_scope=NicheDossierScope.PRODUCTION_PACKAGE,
    )
    assert dossier.overall_verdict == NicheGateVerdict.BLOCK
    assert NicheGateKey.VISUAL in dossier.missing_gate_keys
    assert NicheReasonCode.MANDATORY_NICHE_GATE_EVIDENCE_MISSING in dossier.reason_codes


def test_editorial_idea_context_contract_is_bounded_without_effective_project_context() -> (
    None
):
    contract = AgentContextContractRegistry().resolve(
        "EditorialIdeaResearchAgent",
        task_type="editorial_idea_research",
        lane="cheap_structured",
    )
    sections = {
        name: {"digest_type": name, "content": "bounded"}
        for name in contract.required_context_sections
    }
    result = PromptBudgetGate().apply(
        contract=contract,
        sections=sections,
        initial_omitted=[],
    )
    assert result.status == "OK"
    assert "effective_channel_runtime_digest" not in result.sections
    assert contract.required_context_sections[0] == "niche_contract_digest"


def test_m5_context_builder_exposes_semantic_digest_and_budgeted_agent_pack_offline() -> (
    None
):
    scope = _authority()
    scope.profile.version = 2
    scope.channel.name = "Small Team AI"
    scope.channel.primary_language = "en"
    scope.channel.target_market = "US"
    scope.snapshot.compiled_payload["channel_scoped_policy"].update(
        {
            "policy_version": "small-team-ai.channel-policy.v2",
            "visual_source_policy_binding": {
                "schema_version": "ch1-flex.visual-source-policy-binding.v2",
                "niche_visual_source_profile": "STOCK_ASSISTED",
            },
        }
    )
    scope.snapshot.compiler_version = "nich1-offline"
    scope.snapshot.content_hash = nich1_stable_hash(scope.snapshot.compiled_payload)
    scope.slot.slot_date = date(2026, 7, 19)
    scope.slot.slot_type = "RESEARCH"
    scope.slot.target_platforms = ["YOUTUBE"]
    scope.slot.format_hint = "explainer"
    scope.slot.risk_level = "LOW"

    class _Session:
        def __init__(self):
            self.rows = {
                (ChannelWorkspace, scope.channel.id): scope.channel,
                (ChannelProfileVersion, scope.profile.id): scope.profile,
                (CompiledChannelPolicySnapshot, scope.snapshot.id): scope.snapshot,
                (EditorialCalendarSlot, scope.slot.id): scope.slot,
                (ContentCategory, scope.category.id): scope.category,
            }

        def get(self, model, identifier):
            return self.rows.get((model, identifier))

    plan = SimpleNamespace(
        id=uuid.uuid4(),
        purpose="EDITORIAL_RESEARCH",
        company_id=scope.channel.company_id,
        channel_workspace_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        policy_snapshot_id=scope.snapshot.id,
        video_project_id=None,
        editorial_calendar_slot_id=scope.slot.id,
        allowed_sources=[
            "channel_profile",
            "policy_snapshot",
            "editorial_slot",
            "niche_contract_digest",
        ],
    )
    result = ResourceResolverService(_Session())._build_scoped_pack_content(plan)
    pack = result["pack_content"]
    digest = pack["niche_contract_digest"]
    assert digest["primary_niche"] == "Practical AI for small professional teams"
    assert digest["content_pillar_key"] == scope.slot.content_pillar
    assert digest["series_key"] == scope.slot.series_key
    assert pack["prompt_budget_report"]["budget_status"] == "OK"
    assert set(pack["agent_context_pack"]["digests"]) >= {
        "niche_contract_digest",
        "editorial_slot_digest",
        "runtime_guard_digest",
        "evidence_digest",
        "common_skill_digest",
    }


def test_strict_context_builder_rejects_caller_authority_overrides() -> None:
    scope = _authority()
    scope.profile.version = 2
    scope.channel.name = "Small Team AI"
    scope.channel.primary_language = "en"
    scope.channel.target_market = "US"
    scope.snapshot.compiled_payload["channel_scoped_policy"].update(
        {
            "policy_version": "small-team-ai.channel-policy.v2",
            "visual_source_policy_binding": {
                "schema_version": "ch1-flex.visual-source-policy-binding.v2",
                "niche_visual_source_profile": "STOCK_ASSISTED",
            },
        }
    )
    scope.snapshot.compiler_version = "nich1-offline"
    scope.snapshot.content_hash = nich1_stable_hash(scope.snapshot.compiled_payload)
    scope.slot.slot_date = date(2026, 7, 19)
    scope.slot.slot_type = "RESEARCH"
    scope.slot.target_platforms = ["YOUTUBE"]
    scope.slot.risk_level = "LOW"
    plan_id = uuid.uuid4()
    plan = SimpleNamespace(
        id=plan_id,
        purpose="EDITORIAL_RESEARCH",
        company_id=scope.channel.company_id,
        channel_workspace_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        policy_snapshot_id=scope.snapshot.id,
        video_project_id=None,
        editorial_calendar_slot_id=scope.slot.id,
        allowed_sources=[
            "channel_profile",
            "policy_snapshot",
            "editorial_slot",
            "niche_contract_digest",
        ],
        excluded_sources=[],
    )

    class _Session:
        def get(self, model, identifier):
            values = {
                (RetrievalPlanSnapshot, plan_id): plan,
                (ChannelWorkspace, scope.channel.id): scope.channel,
                (ChannelProfileVersion, scope.profile.id): scope.profile,
                (CompiledChannelPolicySnapshot, scope.snapshot.id): scope.snapshot,
                (EditorialCalendarSlot, scope.slot.id): scope.slot,
                (ContentCategory, scope.category.id): scope.category,
            }
            return values.get((model, identifier))

    service = ResourceResolverService(_Session())
    with pytest.raises(
        ValidationFailureError,
        match="NICH1_CONTEXT_AUTHORITY_OVERRIDE_FORBIDDEN",
    ):
        service.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan_id,
                pack_content={"niche_contract_digest": {"forged": True}},
            )
        )
    with pytest.raises(
        ValidationFailureError,
        match="NICH1_CONTEXT_POLICY_REFS_CALLER_FORBIDDEN",
    ):
        service.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan_id,
                policy_refs=[
                    {
                        "type": "compiled_channel_policy_snapshot",
                        "id": str(uuid.uuid4()),
                    }
                ],
            )
        )


def test_script_contract_digest_projects_bounded_niche_fields_and_ref_hash() -> None:
    scope = _authority()
    digest = _digest(scope)
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        context_hash="a" * 64,
        market_locale_context_json={
            "content_language": "en",
            "primary_market": "US",
            "locale": "en-US",
        },
        audience_context_json={
            "audience_level": "professional",
            "audience_pain_points": digest.audience_pain_points,
        },
        brand_voice_persona_context_json={"tone": "calm", "persona": {}},
        category_runtime_context_json={
            "category_id": str(digest.category_id),
            "sub_niche": digest.category_sub_niche,
            "content_pillar": digest.content_pillar_key,
            "niche_contract_digest_ref": {
                "ref": "artifact-version://niche-digest-v1",
                "content_hash": digest.content_hash,
            },
        },
        safety_forbidden_claims_context_json={
            "forbidden_topics": digest.forbidden_topics,
            "forbidden_claims": [],
        },
        character_identity_context_json={},
    )
    result = build_script_contract_digest(
        snapshot,
        niche_contract_digest=digest.model_dump(mode="json"),
    )
    payload = result["payload"]
    assert payload["primary_niche"] == digest.primary_niche
    assert payload["brand_promise"] == digest.brand_promise
    assert payload["category_id"] == str(digest.category_id)
    assert payload["niche_contract_digest_hash"] == digest.content_hash
    assert "channel_contract_json" not in result

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.contracts.ofv0 import ClaimEvidenceInput, FormatIdentityContractDraftRequest, SyntheticDisclosureInput
from app.core.errors import ForbiddenError, ValidationFailureError
from app.db.models import (
    CloudMediaRef,
    FinalMediaRef,
    FirstScriptedVideoPackage,
    FormatIdentityContract,
    HumanUploadTask,
    MediaRenderJob,
    PaidProviderCallLedger,
    ProviderJobSnapshot,
)
from app.services.ofv0 import (
    ClaimEvidenceLedgerCompiler,
    EpisodeOriginalityManifestBuilder,
    FormatIdentityContractService,
    OriginalityGateService,
    OriginalityReviewReadModelBuilder,
    PlatformNativePackagePlanService,
    SyntheticMediaDisclosureReceiptBuilder,
)
from tests.qualification.conftest import QualificationFactory


def _package(session, scope, *, title: str = "How One Automation Can Save a Small Team 20 Hours Every Week", script: str | None = None, stock_ids: list[str] | None = None) -> FirstScriptedVideoPackage:
    scenes = [
        {"scene_id": "A", "visual_source": "DIAGRAM"},
        {"scene_id": "B", "visual_source": "UI"},
        *[{"scene_id": f"S{i}", "visual_source": "STOCK", "asset_id": asset} for i, asset in enumerate(stock_ids or [])],
    ]
    package = FirstScriptedVideoPackage(
        video_project_id=None,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        effective_context_snapshot_id=None,
        effective_context_hash=None,
        provider_readiness_snapshot_id=None,
        package_status="READY_FOR_HUMAN_REVIEW",
        agent_run_refs=[], prompt_render_run_refs=[], prompt_audit_snapshot_refs=[],
        artifacts={
            "topic": title,
            "script_outline": {"hook": "A small team loses coordination hours every week.", "hook_family": "time-cost diagnosis", "primary_angle": "Expose coordination waste before selecting an automation.", "original_insight_summary": "The useful variable is handoff friction, not tool count.", "viewer_value_summary": "Identify a bottleneck and estimate a safe scenario.", "sections": [{"name": "problem"}, {"name": "mechanism"}, {"name": "constraint"}, {"name": "takeaway"}]},
            "narration_script": {"sentences": [{"sentence_id": "S1", "text": script or "A team can save 20 hours when a specific coordination scenario removes repeat handoffs."}, {"sentence_id": "S2", "text": "This is illustrative and depends on the baseline workflow."}]},
            "visual_plan": {"scenes": scenes},
            "metadata_package": {"title": title, "description": "A practical operational explainer with an illustrative scenario, not a guarantee."},
            "thumbnail_brief": {"composition": "workflow-before-after", "text_pattern": "specific-operational-outcome"},
        },
        limitations=["text-only OFV0 fixture"], risk_limitations_summary={"provider_calls": False, "upload_calls": False}, next_action="OFV0 human review required.",
    )
    session.add(package); session.flush(); return package


def _draft(service: FormatIdentityContractService, scope):
    return service.draft(FormatIdentityContractDraftRequest(channel_id=scope.channel.id, channel_profile_version_id=scope.profile.id, created_by="ChannelAuthorityAgent"))


def _compile(session, package, contract_id, *, claims=None, disclosure=None):
    manifest = EpisodeOriginalityManifestBuilder(session).build(package.id, contract_id=contract_id, episode_topic="How One Automation Can Save a Small Team 20 Hours Every Week")
    ClaimEvidenceLedgerCompiler(session).compile(package.id, claims if claims is not None else [ClaimEvidenceInput(claim_id="hours-20", claim_text="A team can save 20 hours every week in an illustrative scenario with stated baseline assumptions.", claim_scope="TITLE", claim_type="SCENARIO_BASED", source_refs=[{"type": "research_pack", "id": "local-fixture"}], assumptions=["repeatable handoff baseline"], allowed_wording=["can save", "illustrative scenario"], forbidden_wording=["guaranteed"], disclaimer_required=True)])
    SyntheticMediaDisclosureReceiptBuilder(session).build(package.id, disclosure or SyntheticDisclosureInput(stock_media_used=True, disclosure_copy="Synthetic/stock provenance reviewed before manual publish."))
    PlatformNativePackagePlanService(session).ensure_youtube_plans(package.id)
    return manifest


def test_draft_contract_blocks_until_human_approval_and_agent_cannot_approve(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0")
    service = FormatIdentityContractService(db_session)
    draft = _draft(service, scope)
    package = _package(db_session, scope)
    _compile(db_session, package, draft.id)
    gates = OriginalityGateService(db_session).evaluate(package.id)
    assert next(item for item in gates if item.gate_key == "FormatIdentityCompletenessGate").status == "BLOCK"
    with pytest.raises(ForbiddenError, match="SELF_APPROVAL"):
        service.approve(draft.id, decided_by="ChannelAuthorityAgent", actor_kind="AGENT")
    approved = service.approve(draft.id, decided_by="human-operator")
    assert approved.status == "APPROVED"


def test_contract_versioning_freezes_episode_reference_and_no_character_conflict_blocks(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 version")
    service = FormatIdentityContractService(db_session)
    first = _draft(service, scope); service.approve(first.id, decided_by="human")
    package = _package(db_session, scope); manifest = _compile(db_session, package, first.id)
    second = service.draft(FormatIdentityContractDraftRequest(channel_id=scope.channel.id, channel_profile_version_id=scope.profile.id, content={"identity_statement": "new draft"}, created_by="ChannelAuthorityAgent"))
    assert second.contract_version == 2
    assert manifest.format_identity_contract_id == first.id
    # Explicitly create a conflicting draft without mutating Channel Contract/Profile.
    conflict = service.draft(FormatIdentityContractDraftRequest(channel_id=scope.channel.id, channel_profile_version_id=scope.profile.id, content={"character_policy_mode": "RECURRING_SYNTHETIC_HUMAN"}, created_by="ChannelAuthorityAgent"))
    conflict_model = db_session.get(FormatIdentityContract, conflict.id)
    assert conflict_model is not None
    conflict_model.status = "APPROVED"
    conflicting_package = _package(db_session, scope, title="Different conflict fixture", script="Distinct content for policy conflict.")
    _compile(db_session, conflicting_package, conflict.id)
    gates = OriginalityGateService(db_session).evaluate(conflicting_package.id)
    assert next(item for item in gates if item.gate_key == "FormatIdentityCompletenessGate").status == "BLOCK"
    assert manifest.format_identity_contract_id == first.id
    assert db_session.get(FormatIdentityContract, first.id).status == "APPROVED"


def test_exact_duplicate_substance_blocks_even_if_transitions_would_vary(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 duplicate")
    service = FormatIdentityContractService(db_session); contract = _draft(service, scope); service.approve(contract.id, decided_by="human")
    prior_package = _package(db_session, scope, title="Automation saves 20 hours")
    prior = _compile(db_session, prior_package, contract.id); prior.approval_status = "APPROVED"
    package = _package(db_session, scope, title="Automation saves 20 hours")
    _compile(db_session, package, contract.id)
    gates = OriginalityGateService(db_session).evaluate(package.id)
    episode = next(item for item in gates if item.gate_key == "EpisodeOriginalityGate")
    assert episode.status == "BLOCK"
    assert "EXACT_DUPLICATE_SCRIPT_DIGEST" in episode.reason_codes


def test_unique_angle_can_pass_with_shared_channel_style_and_comparison_is_digest_only(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 unique")
    service = FormatIdentityContractService(db_session); contract = _draft(service, scope); service.approve(contract.id, decided_by="human")
    prior = _compile(db_session, _package(db_session, scope, title="Automation saves 20 hours", script="A baseline scenario reveals an approval handoff bottleneck."), contract.id); prior.approval_status = "APPROVED"
    package = _package(db_session, scope, title="How a team reduces procurement approval delays", script="A different mechanism maps approval latency, exception routes, and a measured operational tradeoff.")
    manifest = _compile(db_session, package, contract.id)
    manifest.content["primary_angle"] = "Approval-latency mechanism instead of coordination-hours scenario"; db_session.flush()
    gates = OriginalityGateService(db_session).evaluate(package.id)
    assert next(item for item in gates if item.gate_key == "EpisodeOriginalityGate").status == "REVIEW_REQUIRED"
    read = OriginalityReviewReadModelBuilder(db_session).build(package.id)
    assert read.compared_recent_episodes
    assert read.technical_details["no_raw_previous_scripts_injected"] is True
    assert "narration_script" not in str(read.compared_recent_episodes)


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (None, "PASS"),
        ([], "BLOCK"),
        ([ClaimEvidenceInput(claim_id="bad", claim_text="Save 20 hours guaranteed", claim_type="UNSUPPORTED")], "BLOCK"),
        ([ClaimEvidenceInput(claim_id="scenario", claim_text="20 hours scenario", claim_type="SCENARIO_BASED", allowed_wording=["can"], forbidden_wording=["guaranteed"])], "PASS"),
    ],
)
def test_claim_evidence_gate_enforces_support_and_safe_scenario_wording(db_session, claim, expected) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 claim")
    service = FormatIdentityContractService(db_session); contract = _draft(service, scope); service.approve(contract.id, decided_by="human")
    package = _package(db_session, scope); _compile(db_session, package, contract.id, claims=claim)
    gate = next(item for item in OriginalityGateService(db_session).evaluate(package.id) if item.gate_key == "ClaimEvidenceGate")
    assert gate.status == expected


def test_repeated_exact_stock_sequence_blocks_when_it_becomes_episode_backbone(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 assets")
    service = FormatIdentityContractService(db_session); contract = _draft(service, scope); service.approve(contract.id, decided_by="human")
    prior = _compile(db_session, _package(db_session, scope, title="Prior asset episode", script="Prior distinct script.", stock_ids=["stock-a", "stock-b"]), contract.id); prior.approval_status = "APPROVED"
    package = _package(db_session, scope, title="Different mechanism but copied sequence", script="A materially different explanation with a copied visual asset backbone.", stock_ids=["stock-a", "stock-b"])
    _compile(db_session, package, contract.id)
    gate = next(item for item in OriginalityGateService(db_session).evaluate(package.id) if item.gate_key == "EpisodeOriginalityGate")
    assert gate.status == "BLOCK"
    assert "REPEATED_ASSET_SEQUENCE_BACKBONE" in gate.reason_codes


def test_deceptive_packaging_and_real_person_likeness_block(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 deceptive")
    service = FormatIdentityContractService(db_session); contract = _draft(service, scope); service.approve(contract.id, decided_by="human")
    package = _package(db_session, scope, title="Official guaranteed automation result")
    _compile(db_session, package, contract.id, disclosure=SyntheticDisclosureInput(real_person_likeness_used=True))
    gates = {item.gate_key: item for item in OriginalityGateService(db_session).evaluate(package.id)}
    assert gates["DeceptivePackagingGate"].status == "BLOCK"
    assert gates["SyntheticMediaDisclosureGate"].status == "BLOCK"


def test_negated_disclosure_language_is_not_deceptive_packaging(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 negative packaging")
    service = FormatIdentityContractService(db_session); contract = _draft(service, scope); service.approve(contract.id, decided_by="human")
    package = _package(db_session, scope)
    package.artifacts["metadata_package"]["description"] = "No guarantee, official affiliation, or downloadable resource is claimed."
    _compile(db_session, package, contract.id)
    gate = next(item for item in OriginalityGateService(db_session).evaluate(package.id) if item.gate_key == "DeceptivePackagingGate")
    assert gate.status == "PASS"


def test_disclosure_pending_is_allowed_pre_render_but_blocks_final_publish(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 disclosure")
    service = FormatIdentityContractService(db_session); contract = _draft(service, scope); service.approve(contract.id, decided_by="human")
    package = _package(db_session, scope); _compile(db_session, package, contract.id)
    pre = {item.gate_key: item for item in OriginalityGateService(db_session).evaluate(package.id, final_publish=False)}
    final = {item.gate_key: item for item in OriginalityGateService(db_session).evaluate(package.id, final_publish=True)}
    assert pre["SyntheticMediaDisclosureGate"].status == "PASS"
    assert final["SyntheticMediaDisclosureGate"].status == "BLOCK"


def test_final_reducer_blocks_and_no_execution_or_channel_mutation_occurs(db_session) -> None:
    scope = QualificationFactory(db_session).channel_scope(name="OFV0 boundaries")
    service = FormatIdentityContractService(db_session); draft = _draft(service, scope)
    package = _package(db_session, scope); _compile(db_session, package, draft.id)
    before = {"provider_jobs": db_session.scalar(select(func.count()).select_from(ProviderJobSnapshot)), "paid": db_session.scalar(select(func.count()).select_from(PaidProviderCallLedger)), "render": db_session.scalar(select(func.count()).select_from(MediaRenderJob)), "final": db_session.scalar(select(func.count()).select_from(FinalMediaRef)), "cloud": db_session.scalar(select(func.count()).select_from(CloudMediaRef)), "upload": db_session.scalar(select(func.count()).select_from(HumanUploadTask)), "profile": scope.profile.id, "snapshot": scope.snapshot.id}
    gates = OriginalityGateService(db_session).evaluate(package.id)
    assert next(item for item in gates if item.gate_key == "FinalOriginalityGate").status == "BLOCK"
    with pytest.raises(ValidationFailureError, match="NATIVE_RENDER_PLAN_BLOCKED"):
        OriginalityGateService(db_session).assert_native_render_preflight(package.id)
    assert before["provider_jobs"] == db_session.scalar(select(func.count()).select_from(ProviderJobSnapshot))
    assert before["paid"] == db_session.scalar(select(func.count()).select_from(PaidProviderCallLedger))
    assert before["render"] == db_session.scalar(select(func.count()).select_from(MediaRenderJob))
    assert before["final"] == db_session.scalar(select(func.count()).select_from(FinalMediaRef))
    assert before["cloud"] == db_session.scalar(select(func.count()).select_from(CloudMediaRef))
    assert before["upload"] == db_session.scalar(select(func.count()).select_from(HumanUploadTask))
    assert scope.profile.id == before["profile"] and scope.snapshot.id == before["snapshot"]

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.contracts.dx2 import ProviderStackDriftGuardRead
from app.core.time import utc_now
from app.db.models import (
    AgentContextPackSnapshot,
    AgentOutputValidationRun,
    ChannelProfileVersion,
    EffectiveChannelRuntimeContextSnapshot,
    FinalMediaRef,
    FirstScriptedVideoPackage,
    HumanUploadTask,
    MediaRenderJob,
    PackageRuntimeDisposition,
    ProviderAttempt,
    R3D4GateRun,
    UploadedVideo,
)
from app.main import create_app
from app.services.dx2 import ProviderStackDriftGuard
from app.services.r3d10 import PackageRuntimeDispositionService, RuntimeLTSFreezeVerifier
from tests.qualification.conftest import QualificationFactory
from tests.test_r3d9_runtime_dashboard_ops import _fixture as r3d9_fixture


def _qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _verifier(db_session, **kwargs):
    return RuntimeLTSFreezeVerifier(db_session, application=create_app(), **kwargs).verify()


def _check(result, key: str):
    return {item.invariant_key: item for item in result.invariant_checks}[key]


def _pre_lts_package(db_session, scope) -> FirstScriptedVideoPackage:
    package = FirstScriptedVideoPackage(
        video_project_id=None,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        effective_context_snapshot_id=None,
        effective_context_hash=None,
        provider_readiness_snapshot_id=None,
        package_status="READY_FOR_MEDIA_PROVIDERS",
        agent_run_refs=[{"agent_key": "ScriptPlanningAgent", "route_status": "SUCCESS"}],
        prompt_render_run_refs=[],
        prompt_audit_snapshot_refs=[],
        artifacts={
            "runtime_guard": {
                "real_ollama_agent_run": True,
                "llm_router_only": True,
                "no_media_provider_calls": True,
                "no_upload_or_publish": True,
            }
        },
        limitations=["Pre-LTS text-only rehearsal package."],
        risk_limitations_summary={
            "mock_fallback_used": False,
            "dry_run_success_used": False,
            "media_provider_calls_made": False,
            "upload_or_publish_calls_made": False,
            "no_provider_calls_confirmed": True,
        },
        next_action="Historical package; not eligible for active runtime surface.",
    )
    db_session.add(package)
    db_session.flush()
    return package


def _exclude_package(db_session, package: FirstScriptedVideoPackage, *, disposition: str = "PRE_LTS_HISTORICAL_EXCLUDED") -> PackageRuntimeDisposition:
    return PackageRuntimeDispositionService(db_session).create(
        package_id=package.id,
        disposition=disposition,
        reason_codes=["PRE_LTS_PACKAGE_EXCLUDED_FROM_RUNTIME_SURFACE"],
        decided_by="runtime_lts_freeze_verifier_test",
        evidence={
            "package_status": package.package_status,
            "effective_context_snapshot_id": str(package.effective_context_snapshot_id) if package.effective_context_snapshot_id else None,
            "no_fake_snapshots_or_gates": True,
        },
    )


def test_r3d10_runtime_lts_freeze_verifier_passes_on_canonical_fixture(db_session) -> None:
    r3d9_fixture(db_session, _qualification_factory(db_session))

    result = _verifier(db_session)

    assert result.freeze_status == "PASS", [(item.invariant_key, item.status, item.reason_codes) for item in result.invariant_checks if item.status != "PASS"]
    assert result.blocker_reason_codes == []
    assert _check(result, "provider_stack_drift_guard").status == "PASS"
    assert _check(result, "provider_execution_flags_default_false").status == "PASS"
    assert _check(result, "paid_provider_ledger_no_executed_default").status == "PASS"
    assert _check(result, "allowed_not_executed_does_not_consume_attempt").status == "PASS"
    assert _check(result, "no_youtube_upload_api_route").status == "PASS"
    assert _check(result, "r3d9_frontend_no_job_control_buttons").status == "PASS"
    assert _check(result, "agent_memory_digest_only").status == "PASS"
    assert _check(result, "r3d9_ops_endpoints_get_only").status == "PASS"
    assert result.no_provider_media_upload_execution is True


def test_r3d10_blocks_provider_stack_drift_and_stale_veo_route(db_session) -> None:
    r3d9_fixture(db_session, _qualification_factory(db_session))

    class FakeDriftGuard:
        def check(self):
            return ProviderStackDriftGuardRead(
                generated_at=utc_now(),
                status="PROVIDER_STACK_DRIFT",
                expected_provider_keys=["elevenlabs", "luma_api", "creatomate_growth_10k", "pexels_api"],
                found_active_provider_keys=["elevenlabs"],
                stale_provider_keys=["google-vertex-veo"],
                affected_catalogs={"fixture": [{"provider_key": "google-vertex-veo"}]},
                reason_codes=["STALE_PROVIDER_KEY_ACTIVE"],
                next_action="fix provider stack",
            )

    drift = _verifier(db_session, provider_stack_guard=FakeDriftGuard())
    assert drift.freeze_status == "BLOCKED"
    assert "PROVIDER_STACK_DRIFT" in drift.blocker_reason_codes

    stale_route_guard = ProviderStackDriftGuard(
        catalog_overrides={"media_provider_routing_policy_catalog": [{"job_type": "AI_HERO_GENERATION", "provider_key": "GOOGLE_VERTEX_VEO"}]}
    )
    stale_route = _verifier(db_session, provider_stack_guard=stale_route_guard)
    assert stale_route.freeze_status == "BLOCKED"
    assert "PROVIDER_STACK_DRIFT" in stale_route.blocker_reason_codes


def test_r3d10_blocks_missing_effective_context_snapshot(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    fx["package"].effective_context_snapshot_id = None
    db_session.flush()

    result = _verifier(db_session)

    assert result.freeze_status == "BLOCKED"
    assert "EFFECTIVE_CONTEXT_SNAPSHOT_MISSING" in result.blocker_reason_codes


def test_r3d10_blocks_missing_agent_context_pack(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    db_session.query(AgentContextPackSnapshot).filter(AgentContextPackSnapshot.package_id == fx["package"].id).delete(synchronize_session=False)
    db_session.flush()

    result = _verifier(db_session)

    assert result.freeze_status == "BLOCKED"
    assert "AGENT_CONTEXT_PACK_SNAPSHOT_MISSING" in result.blocker_reason_codes


def test_r3d10_blocks_missing_deterministic_gates(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    db_session.query(R3D4GateRun).filter(R3D4GateRun.package_id == fx["package"].id).delete(synchronize_session=False)
    db_session.flush()

    missing = _verifier(db_session)
    assert missing.freeze_status == "BLOCKED"
    assert "DETERMINISTIC_GATE_MISSING" in missing.blocker_reason_codes


def test_r3d10_blocks_pre_lts_media_ready_package_before_disposition(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    old_package = _pre_lts_package(db_session, fx["scope"])

    result = _verifier(db_session)

    assert result.freeze_status == "BLOCKED"
    assert "EFFECTIVE_CONTEXT_SNAPSHOT_MISSING" in result.blocker_reason_codes
    assert "AGENT_CONTEXT_PACK_SNAPSHOT_MISSING" in result.blocker_reason_codes
    assert "DETERMINISTIC_GATE_MISSING" in result.blocker_reason_codes
    assert "MEDIA_READY_PACKAGE_RUNTIME_DISPOSITION_MISSING" in result.blocker_reason_codes
    assert str(old_package.id) in str(result.evidence_refs)


def test_r3d10_warns_for_pre_lts_historical_excluded_package(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    old_package = _pre_lts_package(db_session, fx["scope"])
    _exclude_package(db_session, old_package)

    result = _verifier(db_session)

    assert result.freeze_status == "PASS", [(item.invariant_key, item.status, item.reason_codes) for item in result.invariant_checks if item.status not in {"PASS", "WARNING"}]
    assert result.blocker_reason_codes == []
    assert "PRE_LTS_PACKAGE_EXCLUDED_FROM_RUNTIME_SURFACE" in result.warning_reason_codes
    disposition_check = _check(result, "package_runtime_disposition_exclusions")
    assert disposition_check.status == "WARNING"
    assert disposition_check.evidence_refs[0]["excluded_package_count"] == 1


def test_r3d10_still_blocks_new_active_package_missing_effective_context(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    active_package = _pre_lts_package(db_session, fx["scope"])
    active_package.package_status = "READY_FOR_HUMAN_REVIEW"
    db_session.flush()

    result = _verifier(db_session)

    assert result.freeze_status == "BLOCKED"
    assert "EFFECTIVE_CONTEXT_SNAPSHOT_MISSING" in result.blocker_reason_codes


def test_r3d10_blocks_excluded_package_with_upload_execution_ref(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    old_package = _pre_lts_package(db_session, fx["scope"])
    _exclude_package(db_session, old_package, disposition="TEST_REHEARSAL_EXCLUDED")
    db_session.add(
        HumanUploadTask(
            company_id=fx["scope"].company.id,
            channel_workspace_id=fx["scope"].channel.id,
            first_scripted_video_package_id=old_package.id,
            destination="YOUTUBE",
            target_platform="YOUTUBE_LONG",
            task_state="READY_FOR_HUMAN_UPLOAD",
            title_snapshot="Historical package should not upload",
            description_snapshot="Execution ref should block exclusion.",
            subtitle_refs=[],
            required_assets=[],
            checklist=[],
            required_checklist=[],
        )
    )
    db_session.flush()

    result = _verifier(db_session)

    assert result.freeze_status == "BLOCKED"
    assert "EXCLUDED_PACKAGE_RUNTIME_EXECUTION_REF_FOUND" in result.blocker_reason_codes
    risk_check = _check(result, "package_runtime_disposition_execution_risk")
    assert "human_upload_tasks" in str(risk_check.evidence_refs)


def test_runtime_disposition_does_not_create_fake_snapshots_gates_or_mutate_contracts(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    old_package = _pre_lts_package(db_session, fx["scope"])
    counts_before = {
        "profiles": db_session.scalar(select(func.count()).select_from(ChannelProfileVersion)),
        "effective": db_session.scalar(select(func.count()).select_from(EffectiveChannelRuntimeContextSnapshot)),
        "packs": db_session.scalar(select(func.count()).select_from(AgentContextPackSnapshot).where(AgentContextPackSnapshot.package_id == old_package.id)),
        "gates": db_session.scalar(select(func.count()).select_from(R3D4GateRun).where(R3D4GateRun.package_id == old_package.id)),
        "provider_attempts": db_session.scalar(select(func.count()).select_from(ProviderAttempt)),
        "media_render_jobs": db_session.scalar(select(func.count()).select_from(MediaRenderJob)),
        "final_media_refs": db_session.scalar(select(func.count()).select_from(FinalMediaRef)),
        "uploaded_videos": db_session.scalar(select(func.count()).select_from(UploadedVideo)),
    }

    _exclude_package(db_session, old_package)
    db_session.flush()

    assert old_package.package_status == "READY_FOR_MEDIA_PROVIDERS"
    assert old_package.effective_context_snapshot_id is None
    assert db_session.scalar(select(func.count()).select_from(ChannelProfileVersion)) == counts_before["profiles"]
    assert db_session.scalar(select(func.count()).select_from(EffectiveChannelRuntimeContextSnapshot)) == counts_before["effective"]
    assert db_session.scalar(select(func.count()).select_from(AgentContextPackSnapshot).where(AgentContextPackSnapshot.package_id == old_package.id)) == counts_before["packs"]
    assert db_session.scalar(select(func.count()).select_from(R3D4GateRun).where(R3D4GateRun.package_id == old_package.id)) == counts_before["gates"]
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == counts_before["provider_attempts"]
    assert db_session.scalar(select(func.count()).select_from(MediaRenderJob)) == counts_before["media_render_jobs"]
    assert db_session.scalar(select(func.count()).select_from(FinalMediaRef)) == counts_before["final_media_refs"]
    assert db_session.scalar(select(func.count()).select_from(UploadedVideo)) == counts_before["uploaded_videos"]


def test_r3d10_blocks_gate_exception(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    gate = db_session.query(R3D4GateRun).filter(R3D4GateRun.package_id == fx["package"].id).first()
    assert gate is not None
    gate.status = "ERROR"
    gate.fail_codes = ["GATE_EXCEPTION"]
    db_session.flush()

    exception = _verifier(db_session)
    assert exception.freeze_status == "BLOCKED"
    assert "DETERMINISTIC_GATE_EXCEPTION" in exception.blocker_reason_codes


def test_r3d10_reviews_unknown_gatekeeper_result_and_blocks_media_ready_conflict(db_session) -> None:
    fx = r3d9_fixture(db_session, _qualification_factory(db_session))
    run = AgentOutputValidationRun(
        package_id=fx["package"].id,
        video_project_id=fx["project"].id,
        agent_key="GatekeeperSoftReviewAgent",
        artifact_type="gatekeeper_review",
        output_type="soft_gatekeeper_review",
        schema_version="r3d4.v1",
        status="OK",
        validation_state="VALID",
        reason_codes=[],
        applied_context_refs_json={},
        evidence_refs_json=[],
        raw_output_hash="raw-hash",
        output_hash="output-hash",
        artifact_hash="artifact-hash",
        canonical_artifact_json={"result": "UNKNOWN"},
        validation_result_json={},
    )
    db_session.add(run)
    db_session.flush()

    review = _verifier(db_session)
    assert review.freeze_status == "REVIEW_REQUIRED", [(item.invariant_key, item.status, item.reason_codes) for item in review.invariant_checks if item.status != "PASS"]
    assert "GATEKEEPER_RESULT_UNKNOWN_REVIEW_REQUIRED" in review.warning_reason_codes

    fx["package"].package_status = "READY_FOR_MEDIA_PROVIDERS"
    db_session.flush()
    blocked = _verifier(db_session)
    assert blocked.freeze_status == "BLOCKED"
    assert "DETERMINISTIC_BLOCK_MEDIA_READY_CONFLICT" in blocked.blocker_reason_codes


def test_r3d10_docs_api_and_dx_import_invariants(db_session) -> None:
    r3d9_fixture(db_session, _qualification_factory(db_session))
    db_session.commit()
    client = TestClient(create_app())

    response = client.get("/ops/runtime-lts-freeze-check")
    assert response.status_code == 200, response.text
    assert response.json()["no_provider_media_upload_execution"] is True

    provider_doc = Path("docs/architecture/provider_stack_freeze.md").read_text(encoding="utf-8")
    assert "Luma API" in provider_doc
    assert "Creatomate Growth 10K" in provider_doc
    assert "final assembly" in provider_doc
    assert "Veo: deferred compatibility only, not active" in provider_doc

    post_freeze = Path("docs/operations/post_freeze_protocol.md").read_text(encoding="utf-8")
    for token in ("P0", "P1", "P2", "P3"):
        assert token in post_freeze
    assert Path("docs/operations/production_pain_log_policy.md").exists()

    result = _verifier(db_session)
    assert _check(result, "dx1_semantic_imports_and_wrappers").status == "PASS"

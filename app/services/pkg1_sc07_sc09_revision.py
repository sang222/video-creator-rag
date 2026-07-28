from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from app.contracts.native_renderer import (
    CanvasSpec,
    NativeOverlayPlan,
    NativeRenderPlan,
    NativeRenderScene,
    TextSafeRegion,
)
from app.contracts.temporal_authority import CanonicalMediaTimeline
from app.contracts.visual_routing import (
    AuthoritativeOverlayContentKind,
    ExactTextNativeOverlayContract,
    VisualSourceRoute,
)
from app.core.errors import ValidationFailureError
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import (
    canonical_caption_render_hash,
    canonical_plan_hash,
)
from app.services.sc07_sc09_visual_route_audit import (
    AUDIT_INPUT_SNAPSHOT_HASH,
    RUN_ID,
    SCENE_CONTEXT,
    build_requirements,
    run_offline_gate_rehearsal,
)
from app.services.visual_source_routing import stable_hash


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_TYPE = "PKG1_SC07_SC09_REVISION"
SCHEMA_VERSION = "pkg1.sc07-sc09-revision.v1"
BUILDER_VERSION = "pkg1-sc07-sc09-revision-builder/1.0.0"
REVISION_VERSION = 4

SOURCE_PROJECT_ID = "0578b24a-1898-443e-99bf-add89d3e61e0"
SOURCE_REVISION_ID = "88fa9f76-99e8-5ec5-8cdd-63c836031bac"
SOURCE_REVISION_HASH = "0115137e13399ccb627845347959b285c6622cd5a0df5b4a8f85850e0dde2410"
SOURCE_PACKAGE_VERSION_ID = "d8471bc0-7d58-4b39-a1f9-267d7b8a02b1"
SOURCE_PACKAGE_HASH = "7d827b7b37a654639383f21c6b6e5cd634c64c68f87832b3b9907dbd4b1fa07c"
SOURCE_PACKAGE_APPROVAL_ID = "77f2fe34-2099-48ad-88e0-2d74a25bfa9e"
SOURCE_MR1_APPROVAL_ID = "f21fb49d-6695-45f1-be2c-231908f3eb93"
FORBIDDEN_OLD_MR1_APPROVAL_ID = "40193854-8633-45a5-97be-54b380a8c8e5"

CHANNEL_PROFILE_VERSION = {
    "id": "d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711",
    "version": 3,
    "content_hash": "1c96bd4dd254ae04f57ba3d7156eb4bc612aeeedc59d2c8e65dda369cd627640",
}
COMPILED_POLICY_SNAPSHOT = {
    "id": "e6c33d80-f5d8-4f72-9abc-87de3601b89e",
    "version": 4,
    "profile_generation": "CHANNEL_PROFILE_V3",
    "content_hash": "12b66551bd9bdfce1d59d1019ff50bc1c49756b6dc4ab505fde080630b4551bc",
}
CANONICAL_BINDINGS = {
    "channel_profile_version": CHANNEL_PROFILE_VERSION,
    "compiled_channel_policy_snapshot": COMPILED_POLICY_SNAPSHOT,
    "channel_contract": {
        "ref": (
            "compiled-policy-snapshot://e6c33d80-f5d8-4f72-9abc-87de3601b89e/"
            "channel-contract"
        ),
        "content_hash": "47ef8716145fb781471293d864f82cc8721a6e79f466a31e1ce0351c20b2b988",
    },
    "niche_contract_digest": {
        "artifact_version_id": "18f105aa-c0fc-4d88-ad98-c88371b6c229",
        "content_hash": "c7d211b2cdaaf91b15b038c8e3d87685b30c16b18509e6845fd6307998494a7a",
    },
    "target_market_profile": {
        "ref": "target-market-profile://small-team-ai/v1",
        "version": 1,
        "content_hash": "d456033a947408f671b328f9c5f5589ae86ea4529caf60b18c3d913058d1bb9e",
    },
    "target_market_digest": {
        "ref": "target-market-digest://small-team-ai/profile-v1",
        "content_hash": "244989186381a71c4eda812743b3b095426397ae0cdfb791641b2875918014f0",
    },
    "niche_alignment_dossier": {
        "artifact_version_id": "7f9381e8-0697-4b77-9f55-d25d72afd547",
        "content_hash": "d6d82fc26373f46e13c75d61068b18c3f23176bb21046ef193240e84bb29703d",
    },
    "market_alignment_dossier": {
        "artifact_version_id": "dba5a8cd-ca61-49e8-b662-c27ad7f02959",
        "content_hash": "57b87274528a909db91417071aae687baa60d33ee9aac90731819cf5bbd4c969",
    },
    "destination_binding": {
        "ref": "destination-binding://small-team-ai/v1",
        "version": 1,
        "content_hash": "411aae66418315da8e6a0bf2cd23e896e89e7cd4827a5b54c36c0437ad63efab",
        "destination_status": "PENDING_PLATFORM_ID",
    },
    "visual_direction_contract": {
        "artifact_version_id": "24a1ca16-cdaa-4b2e-ba4a-158613dcd267",
        "content_hash": "e62c2141434a7ea892453ba632f32b7461e610af8e2844536963e8d48434c317",
    },
}

SOURCE_ARTIFACTS = {
    "scene_visual_intent": {
        "artifact_version_id": "b0e8b068-b79b-4854-81b7-15e68df0992f",
        "content_hash": "55ad4f4725b66f7e1cf7e770df1ee7e0437710925d63e433c00c2ba0441bc712",
    },
    "visual_source_decision_set": {
        "artifact_version_id": "658e43ed-8c8d-43f9-968d-234e41215d99",
        "content_hash": "3e29f3d7e023be9100b62088a6a291ad9457fb70c7cc0967e4d925eef6f1871d",
    },
    "visual_plan": {
        "artifact_version_id": "7186e7ad-3887-4a8d-9fb4-77c59d9be53d",
        "content_hash": "51248879f439dae7116bac6718c833a8da23efacf0fe34a6113194ed6929d617",
    },
    "compiled_asset_request_plan": {
        "artifact_version_id": "ea2724c5-a8b8-4208-a107-59fe7dabaf2a",
        "content_hash": "29436478b9b2e02d12d81795fbe948ca916abd9991d3bca6497db4bba94c7575",
    },
    "provider_execution_plan": {
        "artifact_version_id": "9557bd18-4590-40f2-ab8f-481efdd51d33",
        "content_hash": "fcde893531705ad26109efe703d68f5fffa46ae19577f05ef1179978099d1d31",
    },
    "cost_estimate_snapshot": {
        "artifact_version_id": "d241fd38-935f-4965-94d3-274d1948a163",
        "content_hash": "d8e1eb198e6d8b787a17e3ebd46d8f12a537c0a027d4df2b2bef8ad101830f47",
    },
    "asset_provenance_plan": {
        "artifact_version_id": "41b8c641-fc03-457f-a953-899a657e6ec5",
        "content_hash": "487cd325a3372c92fc549dcbc63dfa8638be8f9c230bfa113f226569852cc294",
    },
    "rights_disclosure_completeness_report": {
        "artifact_version_id": "971025f3-d1ff-40d0-8bba-de47b1742f01",
        "content_hash": "84f8c33a569f1664e62cb98e48be3facb4d9d30549e57c8e28b7035c128d5618",
    },
    "publish_risk_dossier": {
        "artifact_version_id": "c39ac7a0-5dea-402b-b5a9-1495ecc84541",
        "content_hash": "79759b186db73407c081ecbc7da15c8ac1571be63e1422ee0dc380160986e21e",
    },
}

CANONICAL_TIMELINE_PATH = (
    REPO_ROOT
    / "var"
    / "mr1"
    / "runs"
    / RUN_ID
    / "temporal"
    / "canonical-media-timeline.json"
)


def _artifact_ref(
    artifact_type: str,
    payload: Mapping[str, Any],
    *,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = deepcopy(dict(payload))
    content_hash = stable_hash(normalized)
    artifact_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:{PROJECT_TYPE}:{artifact_type}",
        )
    )
    version_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:{PROJECT_TYPE}:{artifact_type}:{content_hash}",
        )
    )
    return {
        "artifact_type": artifact_type,
        "artifact_id": artifact_id,
        "artifact_version_id": version_id,
        "artifact_version_ref": f"artifact-version://{version_id}",
        "version_number": 2 if source else 1,
        "content_hash": content_hash,
        "source_artifact_version_id": (source or {}).get("artifact_version_id"),
        "source_content_hash": (source or {}).get("content_hash"),
        "content": normalized,
    }


def _entry_check() -> None:
    sc07 = json.loads(
        (REPO_ROOT / "reports" / "sc07_visual_route_audit_summary.json").read_text(
            encoding="utf-8"
        )
    )
    sc09 = json.loads(
        (REPO_ROOT / "reports" / "sc09_visual_route_review_summary.json").read_text(
            encoding="utf-8"
        )
    )
    spec = (
        REPO_ROOT / "reports" / "sc07_sc09_package_revision_spec.md"
    ).read_text(encoding="utf-8")
    expected = {
        "SC07_ROUTE_VERDICT": "PEXELS_ROUTE_INVALID",
        "SC07_PREFERRED_SOURCE_ROUTE": "NATIVE_MOTION_GRAPHIC",
        "SC07_REVISED_ROUTE_PREFLIGHT": "PASS",
    }
    if any(sc07["verdicts"].get(key) != value for key, value in expected.items()):
        raise ValidationFailureError("SC07_AUDIT_CHECKPOINT_INVALID")
    expected_sc09 = {
        "SC09_ROUTE_VERDICT": "SC09_PEXELS_ROUTE_INVALID",
        "SC09_QUERY_VERDICT": "REJECT_PEXELS_ROUTE",
        "SC09_PREFERRED_SOURCE_ROUTE": "NATIVE_DIAGRAM",
        "SC09_REVISED_ROUTE_PREFLIGHT": "PASS",
    }
    if any(sc09["verdicts"].get(key) != value for key, value in expected_sc09.items()):
        raise ValidationFailureError("SC09_AUDIT_CHECKPOINT_INVALID")
    required_lines = (
        "SC07_SC09_AUDIT_FINAL=PASS",
        "SC07_SC09_CONTINUITY_DECISION=PASS_DISTINCT_NATIVE_VISUAL_GRAMMARS",
        "SC07_SC09_PACKAGE_REVISION_SPEC=PASS",
        "PROCEED_TO_SC07_SC09_PACKAGE_REVISION=true",
    )
    if any(line not in spec for line in required_lines):
        raise ValidationFailureError("SC07_SC09_AUDIT_CHECKPOINT_INCOMPLETE")


def _revision_identity() -> dict[str, Any]:
    seed = {
        "project_type": PROJECT_TYPE,
        "builder_version": BUILDER_VERSION,
        "source_project_id": SOURCE_PROJECT_ID,
        "source_revision_id": SOURCE_REVISION_ID,
        "source_revision_hash": SOURCE_REVISION_HASH,
        "source_package_version_id": SOURCE_PACKAGE_VERSION_ID,
        "source_package_hash": SOURCE_PACKAGE_HASH,
        "audit_input_snapshot_hash": AUDIT_INPUT_SNAPSHOT_HASH,
        "sc07_requirements_hash": build_requirements("SC-07").content_hash,
        "sc09_requirements_hash": build_requirements("SC-09").content_hash,
    }
    revision_hash = stable_hash(seed)
    revision_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"vcos:{PROJECT_TYPE}:{revision_hash}")
    )
    return {
        "project_type": PROJECT_TYPE,
        "revision_id": revision_id,
        "revision_version": REVISION_VERSION,
        "revision_hash": revision_hash,
        "builder_version": BUILDER_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


def _relative_phases(scene_id: str) -> list[dict[str, Any]]:
    if scene_id == "SC-07":
        return [
            {
                "start_ratio": 0.00,
                "end_ratio": 0.20,
                "stage": "INTRODUCTION",
                "behavior": "Establish NORMAL_PATH and first exception branch.",
            },
            {
                "start_ratio": 0.20,
                "end_ratio": 0.70,
                "stage": "MECHANISM_MOVEMENT",
                "behavior": "Move four exception classes into reason-coded owned queue while original input stays attached.",
            },
            {
                "start_ratio": 0.70,
                "end_ratio": 0.90,
                "stage": "OUTCOME_EMPHASIS",
                "behavior": "Emphasize named owner, pause threshold and visible manual fallback.",
            },
            {
                "start_ratio": 0.90,
                "end_ratio": 1.00,
                "stage": "TRANSITION",
                "behavior": "Settle queue and reduce motion energy into SC-08.",
            },
        ]
    return [
        {
            "start_ratio": 0.00,
            "end_ratio": 0.34,
            "stage": "AUDIT_FIELDS",
            "behavior": "Reveal the five authoritative handoff fields in reading order.",
        },
        {
            "start_ratio": 0.34,
            "end_ratio": 0.55,
            "stage": "BASELINE",
            "behavior": "Bind current baseline and transparent twenty-hour assumption marker.",
        },
        {
            "start_ratio": 0.55,
            "end_ratio": 0.78,
            "stage": "BOUNDED_PILOT",
            "behavior": "Frame bounded pilot with visible fallback.",
        },
        {
            "start_ratio": 0.78,
            "end_ratio": 1.00,
            "stage": "OBSERVED_DECISION",
            "behavior": "Route observed result to STOP or CONTINUE.",
        },
    ]


def _native_plan_spec(
    scene_id: str,
    decision_ref: str,
    decision_hash: str,
) -> dict[str, Any]:
    scene = SCENE_CONTEXT[scene_id]
    route = scene["preferred_route"]
    if scene_id == "SC-07":
        plan_type = "NativeMotionGraphicPlan"
        composition = {
            "layout": "HORIZONTAL_EXCEPTION_FLOW",
            "layers": [
                "channel_background",
                "normal_path",
                "exception_cards",
                "control_queue",
                "owner_lane",
                "outcome_states",
                "native_labels",
                "caption_reserved_region",
            ],
            "nodes": list(scene["native_nodes"]),
            "relationships": [
                "exception leaves normal path",
                "original input remains attached",
                "reason code precedes owner assignment",
                "threshold controls pause",
                "manual fallback remains visible",
            ],
            "camera": "locked orthographic full-frame; no simulated camera parallax",
            "focal_region": {"x": 0.12, "y": 0.12, "width": 0.76, "height": 0.62},
        }
    else:
        plan_type = "NativeDiagramPlan"
        composition = {
            "diagram_type": "FIVE_FIELD_AUDIT_WITH_DECISION_RAIL",
            "layout": "CENTERED_AUDIT_CARD",
            "nodes": list(scene["native_nodes"]),
            "edges": [
                ["TRIGGER", "INPUTS"],
                ["INPUTS", "OWNER"],
                ["OWNER", "SUCCESS_CONDITION"],
                ["SUCCESS_CONDITION", "EXCEPTION_PATH"],
                ["CURRENT_BASELINE", "BOUNDED_PILOT"],
                ["BOUNDED_PILOT", "VISIBLE_FALLBACK"],
                ["OBSERVED_RESULT", "STOP_OR_CONTINUE"],
            ],
            "sequence": [
                "five audit fields",
                "current baseline",
                "bounded pilot",
                "visible fallback",
                "observed result",
                "stop or continue",
            ],
            "state_changes": [
                "UNMAPPED_TO_MAPPED",
                "UNMEASURED_TO_BASELINED",
                "PLANNED_TO_PILOT",
                "OBSERVED_TO_STOP_OR_CONTINUE",
            ],
            "emphasis_states": [
                "ACTIVE_FIELD",
                "BASELINE_LOCKED",
                "FALLBACK_VISIBLE",
                "RESULT_DECIDES",
            ],
            "comparison_structure": "observed result versus current baseline; no universal-saving claim",
        }
    payload = {
        "schema_version": f"{SCHEMA_VERSION}.{plan_type}",
        "scene_id": scene_id,
        "plan_type": plan_type,
        "scene_meaning": scene["narration"],
        "narrative_function": scene["narrative_function"],
        "visual_objective": scene["proposed_semantic_intent"],
        "preferred_source_route": route,
        "fallback_class": "NATIVE_ONLY",
        "fallback": False,
        "provider_execution_required": False,
        "estimated_cost_class": "COST_0_NATIVE",
        "composition": composition,
        "motion_stages": _relative_phases(scene_id),
        "entry_behavior": "FADE_SOFT",
        "exit_behavior": "FADE_SOFT",
        "overlay_plan": {
            "authority": "NATIVE_ONLY",
            "labels": list(scene["native_nodes"]),
            "forbidden_generated_text": True,
            "forbidden_generated_number": True,
            "forbidden_generated_logo": True,
            "forbidden_generated_fake_ui": True,
        },
        "safe_regions": {
            "text": {"x": 0.10, "y": 0.08, "width": 0.80, "height": 0.64},
            "caption": {"x": 0.08, "y": 0.80, "width": 0.84, "height": 0.12},
        },
        "continuity_constraints": (
            ["horizontal branch rhythm", "higher motion energy", "settle before SC-08"]
            if scene_id == "SC-07"
            else ["centered freeze-frame-readable card", "lower motion energy", "final action-plan landing"]
        ),
        "timeline_binding_policy": {
            "duration_source": "CanonicalMediaTimeline",
            "canonical_timeline_ref": (
                f"file://var/mr1/runs/{RUN_ID}/temporal/canonical-media-timeline.json"
            ),
            "canonical_timeline_hash": (
                "eada2c27cadb13eef03d6f160c064807dc63369b77b66192e127a9cbef2bd994"
            ),
            "segment_id": scene_id,
            "relative_phases_only": True,
            "production_milliseconds_persisted_in_plan": False,
        },
        "exact_text_authority": "native_only",
        "exact_number_authority": "native_only",
        "visual_source_decision_ref": decision_ref,
        "visual_source_decision_hash": decision_hash,
        "visual_direction_contract_ref": (
            "artifact-version://24a1ca16-cdaa-4b2e-ba4a-158613dcd267"
        ),
        "visual_direction_contract_hash": CANONICAL_BINDINGS[
            "visual_direction_contract"
        ]["content_hash"],
        "source_class": "NATIVE_AUTHORED",
        "external_provider": False,
        "generated_evidence_authority": False,
        "rights_owner": "VCOS_CURRENT_PRODUCTION_AUTHORITY",
        "asset_file_state": "NOT_CREATED_PLANNING_ONLY",
    }
    return payload


def _overlay(
    scene_id: str,
    route: VisualSourceRoute,
    decision_ref: str,
    decision_hash: str,
) -> tuple[NativeOverlayPlan, list[TextSafeRegion], list[TextSafeRegion]]:
    text_region = TextSafeRegion(
        id=f"{scene_id.lower()}-text-safe",
        x=0.10,
        y=0.08,
        width=0.80,
        height=0.64,
        purpose="Authoritative native workflow labels",
        minimum_contrast_requirement=4.5,
        alignment="center",
    )
    caption_region = TextSafeRegion(
        id=f"{scene_id.lower()}-caption-safe",
        x=0.08,
        y=0.80,
        width=0.84,
        height=0.12,
        purpose="Reserved canonical caption area",
        minimum_contrast_requirement=4.5,
        alignment="bottom-center",
    )
    label_ref = (
        f"script-segment://4c0ac729-32c5-4005-9078-013b399e8802/"
        f"{SCENE_CONTEXT[scene_id]['segment_id']}"
    )
    authoritative_refs = [label_ref]
    kinds = [AuthoritativeOverlayContentKind.WORKFLOW_LABEL]
    exact_number = scene_id == "SC-09"
    if exact_number:
        authoritative_refs.append("script-authority://SC-09/five-fields-and-twenty-hours")
        kinds.append(AuthoritativeOverlayContentKind.NUMBER)
    exact_payload = {
        "scene_id": scene_id,
        "source_decision_ref": decision_ref,
        "source_decision_hash": decision_hash,
        "preferred_source_route": route,
        "exact_text_required": True,
        "exact_number_required": exact_number,
        "forbidden_generated_text": True,
        "forbidden_generated_logo": True,
        "forbidden_generated_fake_ui": True,
        "native_overlay_required": True,
        "authoritative_content_kinds": kinds,
        "authoritative_content_refs": authoritative_refs,
    }
    exact = ExactTextNativeOverlayContract(
        **exact_payload,
        content_hash=stable_hash(exact_payload),
    )
    overlay_payload = {
        "plan_id": f"native-overlay://{PROJECT_TYPE}/{scene_id}",
        "scene_id": scene_id,
        "source_decision_ref": decision_ref,
        "source_decision_hash": decision_hash,
        "preferred_source_route": route,
        "exact_text_contract": exact,
        "text_safe_regions": [text_region],
        "reserved_overlay_regions": [caption_region],
        "overlay_content_refs": authoritative_refs,
        "native_overlay_required": True,
    }
    overlay = NativeOverlayPlan(
        **overlay_payload,
        content_hash=stable_hash(overlay_payload),
    )
    return overlay, [text_region], [caption_region]


def _projected_timeline() -> CanonicalMediaTimeline:
    source = json.loads(CANONICAL_TIMELINE_PATH.read_text(encoding="utf-8"))
    source["segments"] = [
        item
        for item in source["segments"]
        if item["segment_id"] in {"SC-07", "SC-09"}
    ]
    cues = [
        cue
        for segment in source["segments"]
        for cue in segment.get("caption_cues", [])
    ]
    caption_compilation_hash = stable_hash(cues)
    source["qc_metrics"] = {
        **source["qc_metrics"],
        "caption_compilation_hash": caption_compilation_hash,
        "caption_compilation_ref": (
            f"caption-compilation:{caption_compilation_hash}"
        ),
        "caption_render_payload_hash": canonical_caption_render_hash(cues),
        "scene_anchor_count": 2,
        "scene_timeline_contiguous": False,
        "offline_projection_only": True,
        "source_canonical_timeline_hash": source["timeline_hash"],
    }
    source["compilation_warnings"] = [
        *source.get("compilation_warnings", []),
        "OFFLINE_SC07_SC09_PROJECTION_NOT_PRODUCTION_AUTHORITY",
    ]
    source["timeline_hash"] = stable_hash(
        {key: value for key, value in source.items() if key != "timeline_hash"}
    )
    return CanonicalMediaTimeline.model_validate(source)


def compile_native_rehearsal(
    revision_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = dict(revision_identity or _revision_identity())
    timeline = _projected_timeline()
    scenes: list[NativeRenderScene] = []
    for scene_id in ("SC-07", "SC-09"):
        gate = run_offline_gate_rehearsal(scene_id)
        route = VisualSourceRoute(gate["preferred_source_route"])
        decision_ref = (
            f"visual-source-decision://{identity['revision_id']}/{scene_id}"
        )
        overlay, text_regions, reserved_regions = _overlay(
            scene_id,
            route,
            decision_ref,
            gate["decision_hash"],
        )
        timeline_segment = next(
            item for item in timeline.segments if item.segment_id == scene_id
        )
        scenes.append(
            NativeRenderScene(
                scene_id=scene_id,
                source_segment_ids=[scene_id],
                narration_start_ms=timeline_segment.scene_start_ms,
                narration_end_ms=timeline_segment.scene_end_ms,
                duration_ms=timeline_segment.target_scene_duration_ms,
                visual_treatment="DIAGRAM",
                layout_type=(
                    "HORIZONTAL_EXCEPTION_FLOW"
                    if scene_id == "SC-07"
                    else "CENTERED_AUDIT_CARD"
                ),
                animation_type="HIGHLIGHT" if scene_id == "SC-07" else "REVEAL_UP",
                transition_in="FADE_SOFT",
                transition_out="FADE_SOFT",
                emphasis_targets=list(SCENE_CONTEXT[scene_id]["native_nodes"]),
                caption_behavior="CANONICAL_BURN_IN",
                safe_area_policy="VSR1_TEXT_AND_CAPTION_SAFE",
                originality_role="PRIMARY_NATIVE_EXPLANATION",
                scene_notes="Offline compiler rehearsal; milliseconds derived from projected canonical authority only.",
                visual_routing_mode="VSR1_STRICT",
                source_decision_ref=decision_ref,
                source_decision_hash=gate["decision_hash"],
                preferred_source_route=route,
                exact_text_required=True,
                exact_number_required=scene_id == "SC-09",
                forbidden_generated_text=True,
                forbidden_generated_logo=True,
                forbidden_generated_fake_ui=True,
                text_safe_regions=text_regions,
                reserved_overlay_regions=reserved_regions,
                eligibility_gate_refs=[
                    f"visual-routing-gate://{scene_id}/pexels-prohibited",
                    f"visual-routing-gate://{scene_id}/diagram-suitability-pass",
                    f"visual-routing-gate://{scene_id}/evidence-truth-pass",
                ],
                native_overlay_required=True,
                native_overlay_plan=overlay,
            )
        )
    metrics = timeline.qc_metrics
    plan = NativeRenderPlan(
        plan_id=f"native-render-plan://{identity['revision_id']}/offline-rehearsal",
        plan_version=1,
        package_id=f"revision-draft://{identity['revision_id']}",
        video_project_id=SOURCE_PROJECT_ID,
        company_id="e0b7c806-b39e-4792-bf2e-7e8c6d6ca464",
        channel_id="@SmallTeamAI",
        channel_profile_version_id=CHANNEL_PROFILE_VERSION["id"],
        effective_context_snapshot_id=COMPILED_POLICY_SNAPSHOT["id"],
        effective_context_hash=COMPILED_POLICY_SNAPSHOT["content_hash"],
        format_identity_contract_ref="format-identity://small-team-ai/operator-series",
        format_identity_contract_hash=(
            "33a09f529b333653a66a35e1d346720e977b3196ef587d68f59cacea8822d048"
        ),
        episode_originality_manifest_ref=f"package-source://{SOURCE_PACKAGE_VERSION_ID}",
        episode_originality_manifest_hash=SOURCE_PACKAGE_HASH,
        final_originality_gate="PASS",
        claim_evidence_ledger_refs=["source-package://claims/unchanged"],
        synthetic_media_disclosure_receipt_ref=None,
        script_ref="artifact-version://4c0ac729-32c5-4005-9078-013b399e8802",
        script_hash="48e89e95a3234f7f3abd1f86a99dd2e8009279e22dea511ede299483baa14fd7",
        srt_ref=metrics["caption_compilation_ref"],
        srt_hash=metrics["caption_compilation_hash"],
        temporal_authority_mode="CANONICAL_STRICT",
        canonical_media_timeline_ref=(
            f"offline-projection://{RUN_ID}/SC-07+SC-09"
        ),
        canonical_media_timeline_hash=timeline.timeline_hash,
        canonical_audio_asset_ref=timeline.audio_asset_id,
        canonical_caption_compilation_ref=metrics["caption_compilation_ref"],
        canonical_caption_compilation_hash=metrics["caption_compilation_hash"],
        canonical_caption_render_payload_hash=metrics[
            "caption_render_payload_hash"
        ],
        scene_timing_source="CANONICAL_MEDIA_TIMELINE",
        caption_timing_source="CANONICAL_MEDIA_TIMELINE",
        parallel_timing_inputs=[],
        visual_plan_ref=f"visual-plan-revision://{identity['revision_id']}",
        visual_plan_hash=stable_hash(
            {
                "source": SOURCE_ARTIFACTS["visual_plan"],
                "affected_scenes": ["SC-07", "SC-09"],
            }
        ),
        visual_direction_contract_ref=(
            "artifact-version://24a1ca16-cdaa-4b2e-ba4a-158613dcd267"
        ),
        visual_direction_contract_hash=CANONICAL_BINDINGS[
            "visual_direction_contract"
        ]["content_hash"],
        creative_gate_results={
            "SceneSemanticMatchGate": "PASS",
            "VisualContinuityGate": "PASS",
            "AssetAdjacencyGate": "PASS",
        },
        canvas_spec=CanvasSpec(width=1920, height=1080, fps=30),
        scenes=scenes,
        global_motion_policy={
            "motion_pack": "NativeMotionPack_v1",
            "relative_phase_authority": True,
        },
        caption_policy={"authority": "CANONICAL_MEDIA_TIMELINE"},
        audio_policy={"preset": "voice_only_basic", "audio_reuse_not_decided_here": True},
        output_profiles=["YT_LONG_1080P30_SDR_H264_VT"],
        character_policy_mode="NO_CHARACTER",
        purpose="PKG1_SC07_SC09_OFFLINE_COMPILATION_REHEARSAL",
        production_eligible=True,
        status="VALIDATED",
        created_at=datetime(2026, 7, 25, tzinfo=UTC),
        created_by="codex-offline-revision-builder",
    )
    plan.content_hash = canonical_plan_hash(plan)
    compiler = NativeMotionCompiler()
    gate_results = compiler.validator.validate(
        plan,
        execution=True,
        canonical_timeline=timeline,
    )
    blocking = [item for item in gate_results if item.verdict == "BLOCK"]
    if blocking:
        raise ValidationFailureError(
            "SC07_SC09_NATIVE_COMPILATION_BLOCKED:"
            + ",".join(
                code
                for item in blocking
                for code in item.reason_codes
            )
        )
    manifest = compiler.compile(plan, canonical_timeline=timeline)
    by_scene = {item["scene_id"]: item for item in manifest.compiled_scenes}
    return {
        "schema_version": "pkg1.sc07-sc09-native-rehearsal.v1",
        "offline_projection_only": True,
        "production_timing_authority_unchanged": True,
        "source_canonical_timeline_hash": (
            "eada2c27cadb13eef03d6f160c064807dc63369b77b66192e127a9cbef2bd994"
        ),
        "projected_timeline_hash": timeline.timeline_hash,
        "native_render_plan_hash": plan.content_hash,
        "compiled_manifest_hash": manifest.manifest_hash,
        "compiler_version": manifest.compiler_version,
        "motion_pack_version": manifest.motion_pack_version,
        "renderer_input_eligible": manifest.production_eligible,
        "gate_results": [
            item.model_dump(mode="json")
            for item in gate_results
        ],
        "scene_results": {
            "SC-07": {
                "result": "PASS",
                "route": by_scene["SC-07"]["visual_routing"][
                    "preferred_source_route"
                ],
                "timing_source": "OFFLINE_PROJECTED_CANONICAL_TIMELINE",
                "motion_preset": by_scene["SC-07"]["motion_preset"],
                "overlay_hash": stable_hash(manifest.overlay_schedule[0]),
            },
            "SC-09": {
                "result": "PASS",
                "route": by_scene["SC-09"]["visual_routing"][
                    "preferred_source_route"
                ],
                "timing_source": "OFFLINE_PROJECTED_CANONICAL_TIMELINE",
                "motion_preset": by_scene["SC-09"]["motion_preset"],
                "overlay_hash": stable_hash(manifest.overlay_schedule[1]),
            },
        },
        "provider_calls": 0,
        "render_calls": 0,
    }


def build_revision_bundle() -> dict[str, Any]:
    _entry_check()
    identity = _revision_identity()
    rehearsals = {
        scene_id: run_offline_gate_rehearsal(scene_id)
        for scene_id in ("SC-07", "SC-09")
    }
    decisions = {
        scene_id: {
            "scene_id": scene_id,
            "preferred_source_route": result["preferred_source_route"],
            "fallback_class": "NATIVE_ONLY",
            "fallback": False,
            "provider_execution_required": False,
            "provider_execution_allowed": False,
            "estimated_cost_class": "COST_0_NATIVE",
            "requirements_hash": result["requirements_hash"],
            "decision_hash": result["decision_hash"],
            "pexels_result": result["pexels_result"],
            "routing_reason_codes": result["pexels_reason_codes"]
            + result["diagram_reason_codes"],
        }
        for scene_id, result in rehearsals.items()
    }
    decision_patch = _artifact_ref(
        "visual_source_decision_set",
        {
            "schema_version": SCHEMA_VERSION,
            "revision_mode": "PATCH_OVER_EXACT_SOURCE",
            "source": SOURCE_ARTIFACTS["visual_source_decision_set"],
            "affected_scenes": decisions,
            "unaffected_scene_set": ["SC-01", "SC-02", "SC-03", "SC-04", "SC-05", "SC-06", "SC-08"],
            "unaffected_scenes_exact": True,
        },
        source=SOURCE_ARTIFACTS["visual_source_decision_set"],
    )
    intents = {
        scene_id: {
            "scene_id": scene_id,
            "segment_id": SCENE_CONTEXT[scene_id]["segment_id"],
            "segment_text_hash": SCENE_CONTEXT[scene_id]["segment_text_hash"],
            "semantic_intent": SCENE_CONTEXT[scene_id]["proposed_semantic_intent"],
            "scene_meaning": SCENE_CONTEXT[scene_id]["narration"],
            "scene_class": SCENE_CONTEXT[scene_id]["scene_class"],
            "narrative_function": SCENE_CONTEXT[scene_id]["narrative_function"],
            "editorial_intent": SCENE_CONTEXT[scene_id]["editorial_intent"],
            "requirements_hash": rehearsals[scene_id]["requirements_hash"],
        }
        for scene_id in ("SC-07", "SC-09")
    }
    intent_patch = _artifact_ref(
        "scene_visual_intent",
        {
            "schema_version": SCHEMA_VERSION,
            "revision_mode": "PATCH_OVER_EXACT_SOURCE",
            "source": SOURCE_ARTIFACTS["scene_visual_intent"],
            "affected_scenes": intents,
            "unaffected_scene_set": ["SC-01", "SC-02", "SC-03", "SC-04", "SC-05", "SC-06", "SC-08"],
            "unaffected_scenes_exact": True,
        },
        source=SOURCE_ARTIFACTS["scene_visual_intent"],
    )
    native_specs: dict[str, dict[str, Any]] = {}
    for scene_id in ("SC-07", "SC-09"):
        decision_ref = (
            f"{decision_patch['artifact_version_ref']}#"
            f"{scene_id}"
        )
        native_specs[scene_id] = _artifact_ref(
            (
                "sc07_native_motion_plan"
                if scene_id == "SC-07"
                else "sc09_native_diagram_plan"
            ),
            _native_plan_spec(
                scene_id,
                decision_ref,
                rehearsals[scene_id]["decision_hash"],
            ),
        )
    continuity = _artifact_ref(
        "sc07_sc09_visual_continuity_evidence",
        {
            "schema_version": "pkg1.sc07-sc09-continuity.v1",
            "decision": "PASS_DISTINCT_NATIVE_VISUAL_GRAMMARS",
            "sc07": {
                "composition": "HORIZONTAL_EXCEPTION_FLOW",
                "rhythm": "HIGHER_MOTION_BRANCH_QUEUE",
                "meaning_structure": "STATE_TRANSITION_AND_CONTROL",
            },
            "sc09": {
                "composition": "CENTERED_AUDIT_CARD",
                "rhythm": "LOWER_MOTION_SEQUENTIAL_REVEAL",
                "meaning_structure": "AUDIT_FIELDS_AND_RESULT_DECISION",
            },
            "shared_language": {
                "visual_direction_contract": CANONICAL_BINDINGS[
                    "visual_direction_contract"
                ],
                "palette_treatment_consistent": True,
                "caption_readability": "PASS",
                "adjacent_transition_compatibility": "PASS",
            },
            "repeated_stock_or_generated_metaphor": False,
            "repetitive_production_risk": "PASS",
        },
    )
    visual_plan = _artifact_ref(
        "visual_plan",
        {
            "schema_version": SCHEMA_VERSION,
            "revision_mode": "PATCH_OVER_EXACT_SOURCE",
            "source": SOURCE_ARTIFACTS["visual_plan"],
            "affected_scenes": {
                "SC-07": {
                    "route": "NATIVE_MOTION_GRAPHIC",
                    "native_plan_ref": native_specs["SC-07"][
                        "artifact_version_ref"
                    ],
                    "native_plan_hash": native_specs["SC-07"]["content_hash"],
                },
                "SC-09": {
                    "route": "NATIVE_DIAGRAM",
                    "native_plan_ref": native_specs["SC-09"][
                        "artifact_version_ref"
                    ],
                    "native_plan_hash": native_specs["SC-09"]["content_hash"],
                },
            },
            "pexels_requests_removed": ["pexels:SC-07", "pexels:SC-09"],
            "old_query_history_deleted": False,
            "fallback_assumptions_removed": True,
            "continuity_evidence_ref": continuity["artifact_version_ref"],
            "continuity_evidence_hash": continuity["content_hash"],
            "unaffected_scenes_exact": True,
        },
        source=SOURCE_ARTIFACTS["visual_plan"],
    )
    asset_requests = _artifact_ref(
        "compiled_asset_request_plan",
        {
            "schema_version": SCHEMA_VERSION,
            "revision_mode": "PATCH_OVER_EXACT_SOURCE",
            "source": SOURCE_ARTIFACTS["compiled_asset_request_plan"],
            "removed_requests": ["pexels:SC-07", "pexels:SC-09"],
            "requests": [
                {
                    "request_type": "NativeMotionGraphicRequest",
                    "scene_id": "SC-07",
                    "visual_source_decision_hash": rehearsals["SC-07"][
                        "decision_hash"
                    ],
                    "native_plan_hash": native_specs["SC-07"]["content_hash"],
                    "timeline_binding_contract": native_specs["SC-07"]["content"][
                        "timeline_binding_policy"
                    ],
                    "output_technical_requirements": {
                        "width": 1920,
                        "height": 1080,
                        "fps": 30,
                        "caption_safe_required": True,
                    },
                    "rights_provenance_classification": "NATIVE_AUTHORED",
                    "provider_call_required": False,
                    "idempotency_key": (
                        f"native:{identity['revision_id']}:SC-07:"
                        f"{native_specs['SC-07']['content_hash']}"
                    ),
                },
                {
                    "request_type": "NativeDiagramRequest",
                    "scene_id": "SC-09",
                    "visual_source_decision_hash": rehearsals["SC-09"][
                        "decision_hash"
                    ],
                    "native_plan_hash": native_specs["SC-09"]["content_hash"],
                    "timeline_binding_contract": native_specs["SC-09"]["content"][
                        "timeline_binding_policy"
                    ],
                    "output_technical_requirements": {
                        "width": 1920,
                        "height": 1080,
                        "fps": 30,
                        "caption_safe_required": True,
                    },
                    "rights_provenance_classification": "NATIVE_AUTHORED",
                    "provider_call_required": False,
                    "idempotency_key": (
                        f"native:{identity['revision_id']}:SC-09:"
                        f"{native_specs['SC-09']['content_hash']}"
                    ),
                },
            ],
            "unaffected_requests_source_hash_bound": True,
        },
        source=SOURCE_ARTIFACTS["compiled_asset_request_plan"],
    )
    provider_plan_diff = {
        "elevenlabs:narration": "REUSE_VALID",
        "elevenlabs:forced_alignment": "REUSE_VALID",
        "pexels:SC-07": "REMOVED_BY_REVISION",
        "pexels:SC-09": "REMOVED_BY_REVISION",
        "google_drive:archive": "REUSE_VALID",
        "google_drive:finalization-supplement": "REUSE_VALID",
        "SC-07:new-external-provider-operation": "NOT_PRESENT",
        "SC-09:new-external-provider-operation": "NOT_PRESENT",
    }
    provider_plan = _artifact_ref(
        "provider_execution_plan",
        {
            "schema_version": SCHEMA_VERSION,
            "revision_mode": "PATCH_OVER_EXACT_SOURCE",
            "source": SOURCE_ARTIFACTS["provider_execution_plan"],
            "operation_classification": provider_plan_diff,
            "removed_operations": ["pexels:SC-07", "pexels:SC-09"],
            "native_operations": {
                "SC-07": {
                    "route": "NATIVE_MOTION_GRAPHIC",
                    "external_provider_attempts": 0,
                },
                "SC-09": {
                    "route": "NATIVE_DIAGRAM",
                    "external_provider_attempts": 0,
                },
            },
            "gemini_image_operations_added": 0,
            "veo_operations_added": 0,
            "fallback": False,
            "provider_substitution_allowed": False,
            "old_attempt_ledgers_reused": False,
        },
        source=SOURCE_ARTIFACTS["provider_execution_plan"],
    )
    cost = _artifact_ref(
        "cost_estimate_snapshot",
        {
            "schema_version": SCHEMA_VERSION,
            "source": SOURCE_ARTIFACTS["cost_estimate_snapshot"],
            "package_revision_ref": f"revision://{identity['revision_id']}",
            "visual_plan_hash": visual_plan["content_hash"],
            "visual_source_decision_set_hash": decision_patch["content_hash"],
            "provider_execution_plan_hash": provider_plan["content_hash"],
            "operations": {
                "SC-07": {
                    "cost_class": "COST_0_NATIVE",
                    "estimated_provider_cost_usd": 0.0,
                    "actual_cost_usd": None,
                },
                "SC-09": {
                    "cost_class": "COST_0_NATIVE",
                    "estimated_provider_cost_usd": 0.0,
                    "actual_cost_usd": None,
                },
            },
            "planned_external_provider_operations_removed": 2,
            "provider_cost_delta_usd": 0.0,
            "historical_actual_cost_invented": False,
        },
        source=SOURCE_ARTIFACTS["cost_estimate_snapshot"],
    )
    provenance = _artifact_ref(
        "asset_provenance_plan",
        {
            "schema_version": SCHEMA_VERSION,
            "source": SOURCE_ARTIFACTS["asset_provenance_plan"],
            "affected_scenes": {
                scene_id: {
                    "source_class": "NATIVE_AUTHORED",
                    "external_provider": False,
                    "generated_evidence_authority": False,
                    "rights_owner": "VCOS_CURRENT_PRODUCTION_AUTHORITY",
                    "asset_file_state": "NOT_CREATED_PLANNING_ONLY",
                    "native_plan_ref": native_specs[scene_id][
                        "artifact_version_ref"
                    ],
                    "native_plan_hash": native_specs[scene_id]["content_hash"],
                }
                for scene_id in ("SC-07", "SC-09")
            },
            "historical_pexels_evidence_preserved": True,
        },
        source=SOURCE_ARTIFACTS["asset_provenance_plan"],
    )
    rights = _artifact_ref(
        "rights_disclosure_completeness_report",
        {
            "schema_version": SCHEMA_VERSION,
            "source": SOURCE_ARTIFACTS[
                "rights_disclosure_completeness_report"
            ],
            "SC-07": {
                "result": "PASS",
                "classification": "NATIVE_AUTHORED",
                "third_party_asset": False,
                "synthetic_media_disclosure_required": False,
            },
            "SC-09": {
                "result": "PASS",
                "classification": "NATIVE_AUTHORED",
                "third_party_asset": False,
                "synthetic_media_disclosure_required": False,
            },
            "asset_files_claimed_to_exist": False,
        },
        source=SOURCE_ARTIFACTS["rights_disclosure_completeness_report"],
    )
    synthetic_disclosure = _artifact_ref(
        "synthetic_media_disclosure_receipt",
        {
            "schema_version": SCHEMA_VERSION,
            "affected_scenes": ["SC-07", "SC-09"],
            "classification": "NOT_REQUIRED_NATIVE_AUTHORED",
            "provider_generated_pixels": False,
            "generated_evidence_authority": False,
        },
    )
    publish_risk = _artifact_ref(
        "publish_risk_dossier",
        {
            "schema_version": SCHEMA_VERSION,
            "source": SOURCE_ARTIFACTS["publish_risk_dossier"],
            "updated_sections": {
                "visual_semantic_risk": "PASS_PEXELS_BLOCKER_REMOVED",
                "provider_execution_risk": "PASS_NO_SC07_SC09_EXTERNAL_PROVIDER",
                "rights_provenance": "PASS_NATIVE_AUTHORED",
                "repetitive_production_risk": "PASS_DISTINCT_GRAMMARS",
                "market_alignment": "PASS_NO_FALSE_MARKET_CONTEXT",
                "package_integrity": "PASS_EXACT_SOURCE_PATCH_BOUND",
                "new_render_precondition": "NATIVE_PLAN_COMPILATION_REQUIRED",
            },
            "unchanged_sections": [
                "claims",
                "promise_risk",
                "research",
                "script",
                "metadata",
                "destination",
                "manual_publish_boundary",
            ],
            "destination_status": "PENDING_PLATFORM_ID",
            "upload_ready": False,
            "publish_execution_ready": False,
        },
        source=SOURCE_ARTIFACTS["publish_risk_dossier"],
    )
    native_rehearsal = compile_native_rehearsal(identity)
    gate_matrix = {
        "VisualRealizationCompletenessGate": "PASS",
        "PexelsEligibilityGate": "PASS_PROHIBITED_NOT_SELECTED",
        "DiagramSuitabilityGate": "PASS",
        "EvidenceTruthSourceGate": "PASS_NOT_REQUIRED",
        "VisualNicheAlignmentGate": "PASS",
        "VisualMarketAlignmentGate": "PASS",
        "SemanticMatchGate": "PASS",
        "VisualContinuityGate": "PASS",
        "RepetitiveProductionRiskGate": "PASS",
        "RightsDisclosureCompletenessGate": "PASS",
        "ProviderCostEstimateGate": "PASS",
        "PackageIntegrityGate": "PASS",
    }
    approval_supersession = _artifact_ref(
        "approval_supersession_manifest",
        {
            "schema_version": SCHEMA_VERSION,
            "superseded_by": "SUPERSEDED_BY_SC07_SC09_REVISION",
            "historical_authorities": [
                {
                    "kind": "PKG1_APPROVAL",
                    "id": SOURCE_PACKAGE_APPROVAL_ID,
                    "preserved": True,
                    "execution_reusable": False,
                },
                {
                    "kind": "MR1_APPROVAL",
                    "id": SOURCE_MR1_APPROVAL_ID,
                    "preserved": True,
                    "execution_reusable": False,
                },
                {
                    "kind": "MR1_APPROVAL",
                    "id": FORBIDDEN_OLD_MR1_APPROVAL_ID,
                    "preserved": True,
                    "execution_reusable": False,
                },
            ],
            "old_provider_failure_ledgers_linked_to_old_package": True,
            "consumed_ledgers_mutated": False,
        },
    )
    artifact_map = {
        "scene_visual_intent": intent_patch,
        "visual_source_decision_set": decision_patch,
        "sc07_native_motion_plan": native_specs["SC-07"],
        "sc09_native_diagram_plan": native_specs["SC-09"],
        "visual_continuity_evidence": continuity,
        "visual_plan": visual_plan,
        "compiled_asset_request_plan": asset_requests,
        "provider_execution_plan": provider_plan,
        "cost_estimate_snapshot": cost,
        "asset_provenance_plan": provenance,
        "rights_disclosure_completeness_report": rights,
        "synthetic_media_disclosure_receipt": synthetic_disclosure,
        "publish_risk_dossier": publish_risk,
        "approval_supersession_manifest": approval_supersession,
    }
    revision_hash = stable_hash(
        {
            "identity": identity,
            "source_package_hash": SOURCE_PACKAGE_HASH,
            "artifact_hashes": {
                key: value["content_hash"]
                for key, value in sorted(artifact_map.items())
            },
            "native_compiled_manifest_hash": native_rehearsal[
                "compiled_manifest_hash"
            ],
            "gate_matrix": gate_matrix,
            "canonical_bindings": CANONICAL_BINDINGS,
        }
    )
    identity["revision_content_hash"] = revision_hash
    package_manifest = _artifact_ref(
        "package_manifest",
        {
            "schema_version": SCHEMA_VERSION,
            "project_id": SOURCE_PROJECT_ID,
            "revision": identity,
            "supersedes": {
                "revision_id": SOURCE_REVISION_ID,
                "revision_hash": SOURCE_REVISION_HASH,
                "package_artifact_version_id": SOURCE_PACKAGE_VERSION_ID,
                "package_content_hash": SOURCE_PACKAGE_HASH,
            },
            "artifacts": {
                key: {
                    "artifact_version_id": value["artifact_version_id"],
                    "content_hash": value["content_hash"],
                }
                for key, value in sorted(artifact_map.items())
            },
            "canonical_bindings": CANONICAL_BINDINGS,
            "gate_matrix": gate_matrix,
            "native_rehearsal": {
                "SC07_NATIVE_MOTION_COMPILATION": "PASS",
                "SC09_NATIVE_DIAGRAM_COMPILATION": "PASS",
                "native_render_plan_hash": native_rehearsal[
                    "native_render_plan_hash"
                ],
                "compiled_manifest_hash": native_rehearsal[
                    "compiled_manifest_hash"
                ],
            },
            "PKG1_SC07_SC09_REVISION_TECHNICAL": "PASS",
            "PKG1_SC07_SC09_REVISION_HUMAN_REVIEW": "PENDING",
            "PKG1_SC07_SC09_REVISION_FINAL": "WAITING_HUMAN_REVIEW",
            "PRODUCTION_PACKAGE_APPROVED": False,
            "PROCEED_TO_MR1_REAPPROVAL": False,
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "DESTINATION_STATUS": "PENDING_PLATFORM_ID",
        },
    )
    review_packet = _artifact_ref(
        "pkg1_sc07_sc09_review_packet",
        {
            "schema_version": "pkg1.sc07-sc09-review-packet.v1",
            "revision": identity,
            "old_package": {
                "artifact_version_id": SOURCE_PACKAGE_VERSION_ID,
                "content_hash": SOURCE_PACKAGE_HASH,
            },
            "new_package": {
                "artifact_version_id": package_manifest["artifact_version_id"],
                "content_hash": package_manifest["content_hash"],
            },
            "scene_changes": {
                "SC-07": {
                    "old_route": "PEXELS_VIDEO",
                    "new_route": "NATIVE_MOTION_GRAPHIC",
                    "why_changed": "Pexels best semantic score 0.60 < 0.78; scene is an exception-control mechanism.",
                    "provider_calls_removed": 1,
                    "cost_delta_usd": 0.0,
                    "native_plan_ref": native_specs["SC-07"][
                        "artifact_version_ref"
                    ],
                    "native_plan_hash": native_specs["SC-07"]["content_hash"],
                    "gates": "PASS",
                },
                "SC-09": {
                    "old_route": "PEXELS_VIDEO",
                    "new_route": "NATIVE_DIAGRAM",
                    "why_changed": "Revised generic office query was rejected; five-field audit meaning requires native labels and relationships.",
                    "provider_calls_removed": 1,
                    "cost_delta_usd": 0.0,
                    "native_plan_ref": native_specs["SC-09"][
                        "artifact_version_ref"
                    ],
                    "native_plan_hash": native_specs["SC-09"]["content_hash"],
                    "gates": "PASS",
                },
            },
            "continuity": {
                "result": "PASS",
                "artifact_ref": continuity["artifact_version_ref"],
                "content_hash": continuity["content_hash"],
            },
            "provider_plan_diff": provider_plan_diff,
            "rights_diff": "PEXELS_SUPPORTING_TO_NATIVE_AUTHORED",
            "publish_risk_diff": publish_risk["content"]["updated_sections"],
            "superseded_approvals": approval_supersession["content"][
                "historical_authorities"
            ],
            "remaining_blockers": [
                "EXACT_OPERATOR_REVIEW_REQUIRED",
                "FRESH_MR1_REAPPROVAL_REQUIRED_AFTER_PASS",
                "NATIVE_LOCAL_COMPILATION_AND_RENDER_REQUIRED_DURING_MR1",
                "DESTINATION_PLATFORM_ID_REQUIRED_BEFORE_PUBLISH",
            ],
            "exact_next_action": (
                "Operator reviews exact revision/package/native-plan hashes and returns PASS or REJECT: <reason>."
            ),
            "approval_scope": (
                "Exact PKG1_SC07_SC09_REVISION package planning only; no provider, render, Drive, YouTube or publish execution."
            ),
            "human_review_state": "PENDING",
            "auto_approval_allowed": False,
        },
    )
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "identity": identity,
        "source": {
            "project_id": SOURCE_PROJECT_ID,
            "revision_id": SOURCE_REVISION_ID,
            "revision_hash": SOURCE_REVISION_HASH,
            "package_artifact_version_id": SOURCE_PACKAGE_VERSION_ID,
            "package_content_hash": SOURCE_PACKAGE_HASH,
        },
        "canonical_bindings": deepcopy(CANONICAL_BINDINGS),
        "artifacts": artifact_map,
        "package_manifest": package_manifest,
        "review_packet": review_packet,
        "native_rehearsal": native_rehearsal,
        "gate_matrix": gate_matrix,
        "provider_plan_diff": provider_plan_diff,
        "no_execution_proof": {
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
            "old_consumed_ledgers_mutated": False,
            "sc09_query_submitted": False,
        },
        "technical_status": "PASS",
        "human_review_state": "PENDING",
        "final_state": "WAITING_HUMAN_REVIEW",
        "proceed_to_mr1_reapproval": False,
    }
    bundle["bundle_hash"] = stable_hash(bundle)
    return bundle


def revalidate_bundle(bundle: Mapping[str, Any]) -> bool:
    candidate = deepcopy(dict(bundle))
    expected = candidate.pop("bundle_hash", None)
    return bool(expected) and expected == stable_hash(candidate)


__all__ = [
    "BUILDER_VERSION",
    "CANONICAL_BINDINGS",
    "PROJECT_TYPE",
    "REPO_ROOT",
    "REVISION_VERSION",
    "SOURCE_ARTIFACTS",
    "SOURCE_PACKAGE_HASH",
    "SOURCE_PACKAGE_VERSION_ID",
    "build_revision_bundle",
    "compile_native_rehearsal",
    "revalidate_bundle",
]

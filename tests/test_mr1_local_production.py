from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from app.contracts.temporal_authority import (
    AlignedWord,
    CharacterAlignment,
    ForcedAlignmentEvidence,
    NarrationTimingSeed,
)
from app.services.mr1_local_production import (
    ALL_SCENES,
    MR1_SCENE_VISUAL_BLUEPRINTS,
    MR1LocalProductionContinuation,
)
from app.services.native_ffmpeg_renderer import (
    FFMPEG_FULL_DEFAULT,
    FFPROBE_FULL_DEFAULT,
)
from app.services.native_render_plan import stable_hash
from app.services.temporal_authority import SpokenTextNormalizer


TEXT = "workflow context baseline measure trigger outcome review control evidence"
PEXELS_SCENES = ("SC-04", "SC-07", "SC-09")
NATIVE_SCENES = tuple(scene for scene in ALL_SCENES if scene not in PEXELS_SCENES)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, capture_output=True, text=True)


def _fixture(root: Path, *, with_pexels: bool) -> tuple[dict, dict]:
    root.mkdir(parents=True, exist_ok=True)
    normalized = SpokenTextNormalizer().normalize(
        script_revision_id="artifact-version://script-v1",
        source_text=TEXT,
    )
    duration_ms = 12_000
    audio = root / "narration" / "narration.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            FFMPEG_FULL_DEFAULT,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=320:sample_rate=48000:duration=12",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(audio),
        ]
    )
    audio_hash = _file_hash(audio)
    audio_ref = f"file-sha256:{audio_hash}"

    character_spans: list[CharacterAlignment] = []
    for index, character in enumerate(normalized.spoken_text):
        start = round(index * duration_ms / len(normalized.spoken_text))
        end = round((index + 1) * duration_ms / len(normalized.spoken_text))
        character_spans.append(
            CharacterAlignment(
                character_index=index,
                character=character,
                start_ms=start,
                end_ms=max(start + 1, end),
            )
        )
    timing_payload = {
        "provider_key": "elevenlabs",
        "provider_request_id": "local-fixture-narration-request",
        "audio_asset_ref": audio_ref,
        "audio_duration_ms": duration_ms,
        "source_text_hash": normalized.source_text_hash,
        "spoken_text_hash": normalized.spoken_text_hash,
        "original_character_alignment": [
            item.model_dump(mode="json") for item in character_spans
        ],
        "normalized_character_alignment": [
            item.model_dump(mode="json") for item in character_spans
        ],
        "provider_model_id": "eleven_multilingual_v2",
        "provider_voice_id": "pNInz6obpgDQGcFmaJgB",
        "seed": None,
        "voice_settings": {"speed": 0.9},
        "pronunciation_dictionary_refs": [],
        "response_metadata": {"local_fake_transport": True},
        "timing_available": True,
        "timing_parse_warnings": [],
    }
    timing = NarrationTimingSeed(
        **timing_payload, content_hash=stable_hash(timing_payload)
    )
    forced_words: list[AlignedWord] = []
    for index, token in enumerate(normalized.spoken_tokens, start=1):
        chars = character_spans[token.spoken_span.start : token.spoken_span.end]
        forced_words.append(
            AlignedWord(
                word_id=f"forced-{index:04d}",
                text=token.text,
                start_ms=chars[0].start_ms,
                end_ms=chars[-1].end_ms,
                source_spoken_token_ids=[token.token_id],
            )
        )
    forced_payload = {
        "provider_key": "elevenlabs_forced_alignment",
        "provider_request_id": "local-fixture-alignment-request",
        "provider_request_id_availability": "PRESENT",
        "audio_asset_ref": audio_ref,
        "audio_duration_ms": duration_ms,
        "spoken_text_hash": normalized.spoken_text_hash,
        "words": [item.model_dump(mode="json") for item in forced_words],
        "characters": [],
        "alignment_loss": 0.0,
        "transcript_loss": 0.0,
        "missing_tokens": [],
        "extra_words": [],
        "warnings": [],
        "verification_status": "PASS",
    }
    forced = ForcedAlignmentEvidence(
        **forced_payload, content_hash=stable_hash(forced_payload)
    )

    spoken_tokens = [
        {
            "index": index,
            "segment_id": f"S{index + 1:02d}",
            "text": token.text,
        }
        for index, token in enumerate(normalized.spoken_tokens)
    ]
    visual_scenes = [
        {
            "scene_id": scene_id,
            "segment_refs": [f"S{index:02d}"],
            "semantic_intent": MR1_SCENE_VISUAL_BLUEPRINTS[scene_id]["semantic_intent"],
        }
        for index, scene_id in enumerate(ALL_SCENES, start=1)
    ]
    decisions = [
        {
            "scene_id": scene_id,
            "preferred_source_route": (
                "PEXELS_VIDEO" if scene_id in PEXELS_SCENES else "NATIVE_DIAGRAM"
            ),
            "provider": "pexels_api" if scene_id in PEXELS_SCENES else "native",
            "eligibility": (
                "OBSERVABLE_REALITY_SUPPORTING_FOOTAGE_ONLY"
                if scene_id in PEXELS_SCENES
                else "MECHANISM_WORKFLOW_LABEL_NUMBER_COMPARISON_TIMELINE"
            ),
            "semantic_intent": MR1_SCENE_VISUAL_BLUEPRINTS[scene_id]["semantic_intent"],
            "planned_requests": 1 if scene_id in PEXELS_SCENES else 0,
            "maximum_automated_attempts": 1 if scene_id in PEXELS_SCENES else 0,
        }
        for index, scene_id in enumerate(ALL_SCENES, start=1)
    ]
    hashes = {
        key: stable_hash({"fixture": key})
        for key in (
            "script",
            "spoken",
            "visual_plan",
            "decisions",
            "provider_plan",
            "cost",
            "package",
            "profile",
            "snapshot",
            "effective",
            "rights",
            "disclosure",
            "provenance_plan",
        )
    }
    authority = {
        "approval_id": str(uuid.uuid4()),
        "approval_content_hash": stable_hash({"approval": "mr1"}),
        "approval_ref": "mr1-approval://small-team-ai/local-fixture/v1",
        "project_id": str(uuid.uuid4()),
        "package_artifact_version_id": str(uuid.uuid4()),
        "package_content_hash": hashes["package"],
        "exact_target": {
            "project_id": "fixture-project",
            "package_artifact_version_id": "fixture-package",
            "package_content_hash": hashes["package"],
        },
        "package": {"reused_artifacts": {}, "revised_artifacts": {}},
        "exact_bindings": {
            "channel_profile_version": {
                "id": "profile-v3",
                "ref": "channel-profile-version://profile-v3",
                "content_hash": hashes["profile"],
            },
            "compiled_channel_policy_snapshot": {
                "id": "snapshot-v3",
                "ref": "compiled-channel-policy-snapshot://snapshot-v3",
                "content_hash": hashes["snapshot"],
            },
            "effective_context_snapshot": {
                "ref": "effective-context://fixture",
                "content_hash": hashes["effective"],
            },
        },
        "destination": {
            "channel_handle": "@SmallTeamAI",
            "destination_status": "PENDING_PLATFORM_ID",
        },
        "provider_attempt_scope": {
            "elevenlabs_narration": 1,
            "forced_alignment": 1,
            "pexels_scene_attempts": {scene_id: 1 for scene_id in PEXELS_SCENES},
        },
        "cost_scope": {"hard_cap_usd": 1.0, "currency": "USD"},
        "resolved": {
            "script": {
                "artifact_version_id": "script-v1",
                "content_hash": hashes["script"],
                "content": {"segments": []},
            },
            "spoken_text_normalized": {
                "artifact_version_id": "spoken-v1",
                "content_hash": hashes["spoken"],
                "content": {
                    "normalized_text": normalized.spoken_text,
                    "normalized_text_hash": stable_hash(
                        {"normalized_text": normalized.spoken_text}
                    ),
                    "spoken_tokens": spoken_tokens,
                    "pronunciation_dictionary_refs": [],
                },
            },
            "visual_plan": {
                "artifact_id": "visual-plan-artifact",
                "artifact_version_id": "visual-plan-v1",
                "artifact_version_ref": "artifact-version://visual-plan-v1",
                "version_number": 1,
                "content_hash": hashes["visual_plan"],
                "content": {"scenes": visual_scenes},
            },
            "visual_source_decision_set": {
                "artifact_version_id": "visual-decisions-v1",
                "content_hash": hashes["decisions"],
                "content": {
                    "decisions": decisions,
                    "one_route_per_scene": True,
                    "automatic_pexels_to_ai_fallback": False,
                },
            },
            "provider_execution_plan": {
                "artifact_id": "provider-plan-artifact",
                "artifact_version_id": "provider-plan-v1",
                "artifact_version_ref": "artifact-version://provider-plan-v1",
                "version_number": 1,
                "content_hash": hashes["provider_plan"],
                "content": {
                    "one_route_per_scene": True,
                    "automatic_pexels_to_ai_fallback": False,
                    "external_ai_video_fallback": False,
                    "scene_routes": [
                        {
                            "scene_id": scene_id,
                            "route": (
                                "PEXELS_VIDEO"
                                if scene_id in PEXELS_SCENES
                                else "NATIVE_DIAGRAM"
                            ),
                            "provider": (
                                "pexels_api" if scene_id in PEXELS_SCENES else "native"
                            ),
                            "attempt_cap": (1 if scene_id in PEXELS_SCENES else 0),
                        }
                        for scene_id in ALL_SCENES
                    ],
                },
            },
            "cost_estimate_snapshot": {
                "artifact_version_id": "cost-v1",
                "content_hash": hashes["cost"],
                "content": {"hard_cap": 1.0},
            },
            "rights_disclosure_completeness_report": {
                "artifact_id": "rights-artifact",
                "artifact_version_id": "rights-v1",
                "artifact_version_ref": "artifact-version://rights-v1",
                "version_number": 1,
                "content_hash": hashes["rights"],
                "content": {
                    "planning_state": "PASS",
                    "decision": "PASS",
                    "provider_outputs_claimed": False,
                    "generated_evidence_authority": False,
                },
            },
            "synthetic_media_disclosure_receipt_draft": {
                "artifact_id": "disclosure-artifact",
                "artifact_version_id": "disclosure-v1",
                "artifact_version_ref": "artifact-version://disclosure-v1",
                "version_number": 1,
                "content_hash": hashes["disclosure"],
                "content": {
                    "receipt_status": "PRE_RENDER_PLANNED",
                    "provider_outputs_exist": False,
                    "synthetic_voice_planned": True,
                    "synthetic_image_planned": False,
                    "synthetic_video_planned": False,
                },
            },
            "asset_provenance_plan": {
                "artifact_id": "provenance-plan-artifact",
                "artifact_version_id": "provenance-plan-v1",
                "artifact_version_ref": "artifact-version://provenance-plan-v1",
                "version_number": 1,
                "content_hash": hashes["provenance_plan"],
                "content": {
                    "provider_output_exists": False,
                    "generated_evidence_authority": False,
                },
            },
        },
    }
    candidate_authority_bindings = {
        "schema_version": "mr1.candidate-authority-bindings.v1",
        "package": {
            "artifact_version_id": authority["package_artifact_version_id"],
            "content_hash": authority["package_content_hash"],
        },
        "approval": {
            "approval_decision_id": authority["approval_id"],
            "approval_content_hash": authority["approval_content_hash"],
        },
        "channel_profile_version": dict(
            authority["exact_bindings"]["channel_profile_version"]
        ),
        "compiled_channel_policy_snapshot": dict(
            authority["exact_bindings"]["compiled_channel_policy_snapshot"]
        ),
    }
    for candidate_key, resolved_key in (
        ("rights_disclosure_completeness_report", "rights_disclosure_completeness_report"),
        (
            "synthetic_media_disclosure_receipt_draft",
            "synthetic_media_disclosure_receipt_draft",
        ),
        ("asset_provenance_plan", "asset_provenance_plan"),
        ("visual_plan", "visual_plan"),
        ("provider_execution_plan", "provider_execution_plan"),
    ):
        resolved_artifact = authority["resolved"][resolved_key]
        candidate_authority_bindings[candidate_key] = {
            key: resolved_artifact[key]
            for key in (
                "artifact_id",
                "artifact_version_id",
                "artifact_version_ref",
                "version_number",
                "content_hash",
            )
        }
    candidate_authority_bindings["content_hash"] = stable_hash(
        candidate_authority_bindings
    )
    authority["candidate_authority_bindings"] = candidate_authority_bindings
    outputs: dict = {
        "narration": {
            "provider": "elevenlabs",
            "provider_call_made": True,
            "audio_path": str(audio),
            "audio_asset_ref": audio_ref,
            "audio_sha256": audio_hash,
            "audio_duration_ms": duration_ms,
            "timing_seed": timing.model_dump(mode="json"),
            "temporal_spoken_text_normalized": normalized.model_dump(mode="json"),
        },
        "alignment": {
            "provider": "forced_alignment",
            "provider_call_made": True,
            "audio_sha256": audio_hash,
            "verification_status": "PASS",
            "token_coverage": 1.0,
            "forced_alignment_evidence": forced.model_dump(mode="json"),
            "temporal_spoken_text_normalized": normalized.model_dump(mode="json"),
        },
    }
    if with_pexels:
        colors = {"SC-04": "0x8b1e3f", "SC-07": "0x146c94", "SC-09": "0x588157"}
        for scene_id, color in colors.items():
            path = root / "source_assets" / f"{scene_id}.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            _run(
                [
                    FFMPEG_FULL_DEFAULT,
                    "-hide_banner",
                    "-nostdin",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:s=1920x1080:r=30:d=2",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(path),
                ]
            )
            outputs[f"pexels:{scene_id}"] = {
                "provider": "pexels_api",
                "provider_call_made": True,
                "scene_id": scene_id,
                "route": "PEXELS_VIDEO",
                "local_path": str(path),
                "sha256": _file_hash(path),
                "size_bytes": path.stat().st_size,
                "width": 1920,
                "height": 1080,
                "duration_ms": 2_000,
                "provider_asset_id": f"pexels-{scene_id}",
                "provider_file_id": f"file-{scene_id}",
                "source_page_url": f"https://www.pexels.com/video/{scene_id.lower()}",
                "creator_name": f"Creator {scene_id}",
                "creator_url": f"https://www.pexels.com/@creator-{scene_id.lower()}",
                "creator_ref": f"pexels-creator://creator-{scene_id.lower()}",
                "license_ref": "https://www.pexels.com/license/",
                "rights_policy_ref": "policy://pexels-license/v1",
                "attribution_copy": f"Video by Creator {scene_id} on Pexels",
                "selected_candidate": {"id": f"pexels-{scene_id}"},
            }
    return authority, outputs


def test_temporal_prepass_is_exact_idempotent_and_requires_no_visual_outputs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    authority, outputs = _fixture(root, with_pexels=False)
    continuation = MR1LocalProductionContinuation(
        workspace_root=tmp_path,
        ffmpeg=FFMPEG_FULL_DEFAULT,
        ffprobe=FFPROBE_FULL_DEFAULT,
    )
    run_id = uuid.uuid4()

    first = continuation.prepare_temporal_authority_once(
        run_id=run_id,
        workspace=root,
        authority=authority,
        provider_outputs=outputs,
    )
    second = continuation.prepare_temporal_authority_once(
        run_id=run_id,
        workspace=root,
        authority=authority,
        provider_outputs=outputs,
    )

    assert first == second
    assert first["result"] == "PASS"
    assert first["timing_authority"] == "CANONICAL_MEDIA_TIMELINE"
    assert first["estimated_timing_fallback_used"] is False
    assert first["provider_calls_made_by_continuation"] == 0
    assert [item["scene_id"] for item in first["scene_windows"]] == list(ALL_SCENES)
    assert first["scene_windows"][0]["start_ms"] == 0
    assert first["scene_windows"][-1]["end_ms"] == 12_000
    supporting = first["supporting_visual_subwindows"]
    assert [item["scene_id"] for item in supporting] == list(PEXELS_SCENES)
    windows = {item["scene_id"]: item for item in first["scene_windows"]}
    for item in supporting:
        scene = windows[item["scene_id"]]
        stock = item["stock_context"]
        native = item["native_explanation"]
        assert stock["start_ms"] == scene["start_ms"]
        assert stock["duration_ms"] == scene["duration_ms"] * 20 // 100
        assert stock["end_ms"] == native["start_ms"]
        assert native["end_ms"] == scene["end_ms"]
        assert stock["duration_ms"] > 0
        assert native["duration_ms"] > 0
        assert (
            item["native_mechanism"]
            == MR1_SCENE_VISUAL_BLUEPRINTS[item["scene_id"]]["mechanism"]
        )
    temporal_gate = json.loads(
        (root / "temporal" / "temporal-authority-gate.json").read_text(encoding="utf-8")
    )
    assert (
        temporal_gate["supporting_visual_subwindows_hash"]
        == first["supporting_visual_subwindows_hash"]
    )


def test_full_local_continuation_renders_actual_bytes_and_builds_exact_archive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    authority, outputs = _fixture(root, with_pexels=True)
    (root / "authority.json").write_text(
        json.dumps(authority, indent=2, sort_keys=True), encoding="utf-8"
    )
    evidence = root / "execution_evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "provider_attempt_ledgers.json").write_text(
        json.dumps({"attempts": "all-succeeded"}), encoding="utf-8"
    )
    (evidence / "provider_output_manifest.json").write_text(
        json.dumps({"provider_calls": 5}), encoding="utf-8"
    )
    continuation = MR1LocalProductionContinuation(
        workspace_root=tmp_path,
        ffmpeg=FFMPEG_FULL_DEFAULT,
        ffprobe=FFPROBE_FULL_DEFAULT,
    )
    run_id = uuid.uuid4()

    result = continuation.continue_once(
        run_id=run_id,
        workspace=root,
        authority=authority,
        provider_outputs=outputs,
        resume_from=None,
    )

    assert result["state"] == "READY_FOR_ARCHIVE", result
    assert result["local_provider_calls"] == 0
    assert result["provider_calls_repeated"] is False
    assert result["technical_media_qc"]["result"] == "PASS"
    assert result["creative_media_qc"]["result"] == "REVIEW_REQUIRED"
    candidate = result["review_media_candidate"]
    output = Path(candidate["output_file_ref"])
    assert output.is_file()
    assert _file_hash(output) == candidate["output_sha256"]
    probe = subprocess.run(
        [
            FFPROBE_FULL_DEFAULT,
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(item for item in streams if item["codec_type"] == "video")
    audio = next(item for item in streams if item["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == (
        "h264",
        1920,
        1080,
    )
    assert (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) == (
        "aac",
        48_000,
        2,
    )
    visual_execution = json.loads(
        (root / "assets" / "scene-visual-execution-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    visual_items = {item["scene_id"]: item for item in visual_execution["items"]}
    assert set(visual_items) == set(ALL_SCENES)
    assert {
        scene_id: item["approved_mechanism"] for scene_id, item in visual_items.items()
    } == {
        scene_id: MR1_SCENE_VISUAL_BLUEPRINTS[scene_id]["mechanism"]
        for scene_id in ALL_SCENES
    }
    assert (
        "5 PEOPLE  ×  1 HOUR  ×  4 DAYS  =  20 HOURS"
        in visual_items["SC-01"]["authoritative_labels"]
    )
    assert (
        "ILLUSTRATIVE TOTAL  =  20 HOURS"
        in visual_items["SC-02"]["authoritative_labels"]
    )
    assert "HYPOTHESIS" in visual_items["SC-06"]["authoritative_labels"]
    assert "OBSERVED PEOPLE" in visual_items["SC-08"]["authoritative_labels"]
    assert visual_items["SC-01"]["exact_number_required"] is True
    assert visual_items["SC-02"]["exact_number_required"] is True
    for scene_id in NATIVE_SCENES:
        state = visual_items[scene_id]["frame_state_evidence"]
        assert state["motion_state_change"] is True
        assert len(state["state_semantics"]) == 2
        assert visual_items[scene_id]["mechanism_initial_source"]
    for scene_id in PEXELS_SCENES:
        item = visual_items[scene_id]
        state = item["frame_state_evidence"]
        assert 0 < item["pexels_context_duration_ms"]
        assert item["native_explanation_after_context"] is True
        assert state["stock_to_native_state_change"] is True
        assert (
            state["stock_context"]["decoded_frame_sha256"]
            != state["native_explanation"]["decoded_frame_sha256"]
        )

    plan = json.loads(
        (root / "render" / "native-render-plan.json").read_text(encoding="utf-8")
    )
    plan_scenes = {item["scene_id"]: item for item in plan["scenes"]}
    assert all(item["animation_type"] != "HOLD_STATIC" for item in plan_scenes.values())
    assert all(item["transition_in"] != "CUT" for item in plan_scenes.values())
    assert all(item["exact_text_required"] is True for item in plan_scenes.values())
    assert all(item["native_overlay_required"] is True for item in plan_scenes.values())
    assert all(item["native_overlay_plan"] for item in plan_scenes.values())
    assert plan_scenes["SC-01"]["exact_number_required"] is True
    assert plan_scenes["SC-02"]["exact_number_required"] is True
    compiled = json.loads(
        (root / "render" / "compiled-native-render-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(compiled["overlay_schedule"]) == 9
    assert {item["scene_id"] for item in compiled["overlay_schedule"]} == set(
        ALL_SCENES
    )
    assert all(
        item["result"] == "REVIEW_REQUIRED"
        for item in result["creative_media_qc"]["gate_results"]
    )
    assert all(
        item["metrics"]["actual_review_mp4_sha256"] == candidate["output_sha256"]
        for item in result["creative_media_qc"]["gate_results"]
    )
    roles = {item["logical_role"] for item in result["archive_sources"]}
    assert {
        "MR1_REAL_PRODUCTION_REPORT",
        "MR1_SUMMARY",
        "MR1_REPAIR_CYCLES",
        "MR1_FINAL_REVIEW_MP4",
        "MR1_TECHNICAL_MEDIA_QC",
        "MR1_CREATIVE_MEDIA_QC",
        "MR1_LOCAL_ARCHIVE_MANIFEST",
    }.issubset(roles)
    assert len(roles) == len(result["archive_sources"])
    assert len({item["name"] for item in result["archive_sources"]}) == len(roles)
    assert len({item["archive_path"] for item in result["archive_sources"]}) == len(
        roles
    )

    resumed = continuation.continue_once(
        run_id=run_id,
        workspace=root,
        authority=authority,
        provider_outputs=outputs,
        resume_from=result["resume_from"],
    )
    assert resumed == result
    assert resumed["render_attempts"] == 1

    directive_payload = {
        "schema_version": "mr1.human-repair-directive.v1",
        "run_id": str(run_id),
        "decision": "REJECT",
        "review_round": 2,
        "rejected_output_sha256": candidate["output_sha256"],
        "repair_classes": ["caption", "readability"],
        "operator_reason": "Caption readability needs a deterministic local repair.",
    }
    (root / "human_repair_directive.json").write_text(
        json.dumps(
            {
                **directive_payload,
                "content_hash": stable_hash(directive_payload),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    repaired = continuation.continue_once(
        run_id=run_id,
        workspace=root,
        authority=authority,
        provider_outputs=outputs,
        resume_from="REPAIR_REQUIRED_AFTER_HUMAN_REJECTION",
    )
    assert repaired["state"] == "READY_FOR_ARCHIVE", repaired
    assert repaired["review_round"] == 2
    assert repaired["human_repair_directive_hash"] == stable_hash(directive_payload)
    assert repaired["repaired_from_output_sha256"] == candidate["output_sha256"]
    assert repaired["provider_outputs_reused_for_human_repair"] is True
    assert repaired["provider_calls_repeated"] is False
    assert repaired["local_provider_calls"] == 0
    assert repaired["render_attempts"] == 2
    repaired_candidate = repaired["review_media_candidate"]
    assert repaired_candidate["review_round"] == 2
    assert repaired_candidate["provider_outputs_reused"] is True
    assert repaired_candidate["output_sha256"] != candidate["output_sha256"]
    assert Path(repaired_candidate["output_file_ref"]).name == (
        "mr1-review-candidate-r2.mp4"
    )
    repair_state = json.loads(
        (root / "human_repair_state.json").read_text(encoding="utf-8")
    )
    preserved_output = Path(repair_state["preserved_output_path"])
    assert preserved_output.is_file()
    assert _file_hash(preserved_output) == candidate["output_sha256"]
    repaired_resumed = continuation.continue_once(
        run_id=run_id,
        workspace=root,
        authority=authority,
        provider_outputs=outputs,
        resume_from=repaired["resume_from"],
    )
    assert repaired_resumed == repaired

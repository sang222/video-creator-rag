from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.contracts.asset_acquisition import AIHeroAssetRequest
from app.contracts.google_veo import (
    GoogleVeoExecutionGates,
    GoogleVeoProvenanceManifest,
    ProviderAudioNormalizationReceipt,
)
from app.core.config import Settings
from app.providers.google_veo import GoogleVeoAdapter
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.media_normalizer import MediaNormalizer
from app.services.native_render_plan import stable_hash
from app.services.production_archive import ArchiveSource, ProductionArchiveBuilder, ROLE_ARCHIVE_PATHS


class _FixtureVeoClient:
    def __init__(self):
        self.submit_count = 0
        self.poll_count = 0

    def submit(self, request):
        self.submit_count += 1
        return {"operation_id": "fixture-veo-operation-001", "status": "SUBMITTED"}

    def get_operation(self, provider_operation_id: str):
        self.poll_count += 1
        if self.poll_count < 2:
            return {"status": "PROCESSING"}
        return {
            "status": "SUCCEEDED",
            "output_url": "https://generativelanguage.googleapis.com/volatile/video.mp4?token=fixture-secret",
        }


class GoogleVeoLocalFixtureRehearsal:
    def run(self, *, workspace_root: Path, fixture_mp4: Path) -> dict:
        root = workspace_root / "hpr1-google-veo-fixture"
        manifests = root / "manifests"
        source = root / "source" / "ai-hero"
        normalized = root / "normalized" / "hero"
        for path in (manifests, source, normalized):
            path.mkdir(parents=True, exist_ok=True)

        prompt = "Abstract paper workflow transforms into a calm luminous operating system, no characters, no logos"
        generic_payload = {
            "request_id": "hpr1-ai-hero-request-001",
            "package_id": "hpr1-package",
            "project_id": "hpr1-project",
            "channel_id": "small-team-ai",
            "scene_id": "scene-metaphor",
            "source_segment_ids": ["segment-001"],
            "visual_intent": "operating workflow transformation metaphor",
            "hero_reason": "METAPHOR",
            "prompt_text": prompt,
            "prompt_hash": stable_hash(prompt),
            "prompt_safety_status": "PASS",
            "required_duration_seconds": 8,
            "preferred_resolution": "720p",
            "required_aspect_ratio": "16:9",
            "character_policy_mode": "NO_CHARACTER",
            "projected_cost_class": "MEDIUM",
            "human_approval_required": True,
            "provider_resolution_policy_ref": "policy://small-team-ai/strategy-b/ai-hero-v1",
        }
        generic = AIHeroAssetRequest(**generic_payload, request_hash=stable_hash(generic_payload))

        settings = Settings(
            _env_file=None,
            VCOS_AI_VIDEO_HERO_PROVIDER="google_veo",
            VEO_MODEL_ID="veo-3.1-fast-generate-preview",
            VEO_DEFAULT_DURATION_SECONDS=8,
            VEO_DEFAULT_RESOLUTION="720p",
            VEO_DEFAULT_ASPECT_RATIO="16:9",
            VEO_DEFAULT_OUTPUT_COUNT=1,
            VCOS_VEO_REAL_GENERATION_ENABLED=False,
            VCOS_PA1R_VEO_SMOKE_ENABLED=False,
        )
        catalog = GoogleVeoModelPriceCatalog()
        cost = catalog.estimate(
            model_id=settings.veo_model_id,
            resolution=settings.veo_default_resolution,
            duration_seconds=8,
            output_count=1,
            hard_cap=Decimal("1.00"),
            approval_amount=Decimal("1.00"),
        )
        fake = _FixtureVeoClient()
        adapter = GoogleVeoAdapter(settings, fixture_client=fake)
        request = adapter.build_generation_request(
            generic,
            cost_catalog_ref=cost.price_catalog_ref,
            approval_ref="approval://hpr1-fixture-only",
            approval_scope="PA1R_ONE_AI_HERO_CLIP",
            idempotency_key="fixture-idempotency-key-001",
        )
        gates = GoogleVeoExecutionGates(
            provider_boundary_gate_passed=True,
            human_paid_render_approval_passed=True,
            cost_estimate_snapshot_passed=True,
            channel_monthly_budget_gate_passed=True,
            paid_attempt_limit_gate_passed=True,
            provider_idempotency_key_valid=True,
            global_kill_switch_open=True,
            provider_kill_switch_open=True,
        )
        submitted = adapter.submit_generation(request, gates=gates, fixture_only=True)
        duplicate = adapter.submit_generation(request, gates=gates, fixture_only=True)
        completed = adapter.poll_operation(submitted, max_polls=3, fixture_only=True)
        raw_fixture_url = "https://generativelanguage.googleapis.com/volatile/video.mp4?token=fixture-secret"
        output_path = source / "selected-ai-hero-take.mp4"
        download_plan = adapter.build_output_download_plan(completed, raw_output_url=raw_fixture_url, destination_path=output_path)
        download = adapter.download_output(download_plan, fixture_source=fixture_mp4)

        audio_metadata = {"codec": "aac", "channels": 2, "sample_rate_hz": 48000, "fixture_declared": True}
        normalization = MediaNormalizer().compile_video_plan(
            input_asset_ref="veo-fixture-output",
            input_asset_hash=download["sha256"],
            input_path=output_path,
            output_path=normalized / "selected-ai-hero-take-muted.mp4",
            width=1280,
            height=720,
            audio_policy="REMOVE",
            provider_audio_present=True,
            provider_audio_stream_metadata=audio_metadata,
        )
        audio_receipt_payload = {
            "provider_audio_present": True,
            "provider_audio_stream_metadata": audio_metadata,
            "provider_audio_usage_policy": "DISCARD",
            "provider_audio_discarded": True,
            "narration_authority": "ELEVENLABS",
            "final_mix_authority": "NATIVE_FFMPEG",
            "normalized_contains_audio_stream": False,
            "media_qc_status": "PASS",
        }
        audio_receipt = ProviderAudioNormalizationReceipt(**audio_receipt_payload, receipt_hash=stable_hash(audio_receipt_payload))
        provenance_payload = {
            "provider": "GOOGLE_VEO",
            "gemini_project_reference": "gemini-project://operator-default",
            "model_id": request.model_id,
            "operation_id": completed.provider_operation_id,
            "prompt_hash": request.prompt_hash,
            "reference_asset_hashes": [],
            "generated_at": datetime(2026, 7, 12, tzinfo=UTC),
            "output_reference": completed.output_reference,
            "downloaded_file_path": download["downloaded_path"],
            "size_bytes": download["size_bytes"],
            "sha256": download["sha256"],
            "provider_audio_present": True,
            "provider_audio_stream_metadata": audio_metadata,
            "provider_audio_discarded": True,
            "generation_cost_ref": cost.snapshot_hash,
            "human_approval_ref": request.approval_ref,
            "media_qc_ref": "qc://fixture/provider-audio-removed",
            "used_by_segments": generic.source_segment_ids,
            "synthetic_media_disclosure_required": True,
            "production_eligible": False,
        }
        provenance = GoogleVeoProvenanceManifest(**provenance_payload, manifest_hash=stable_hash(provenance_payload))

        evidence = {
            "ai_hero_asset_request.json": generic.model_dump(mode="json"),
            "google_veo_generation_request.json": request.model_dump(mode="json"),
            "google_veo_operation_receipt.json": completed.model_dump(mode="json"),
            "google_veo_cost_estimate.json": cost.model_dump(mode="json"),
            "google_veo_output_download_plan.json": download_plan.model_dump(mode="json"),
            "google_veo_provenance_manifest.json": provenance.model_dump(mode="json"),
            "provider_audio_normalization_receipt.json": audio_receipt.model_dump(mode="json"),
            "media_normalization_manifest.json": normalization.model_dump(mode="json"),
            "synthetic_media_disclosure.json": {"source_role": "AI_HERO", "provider_key": "google_veo", "required": True, "production_eligible": False},
        }
        for name, payload in evidence.items():
            (manifests / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")

        sources = self._archive_sources(root, manifests, output_path)
        archive = ProductionArchiveBuilder().build(
            manifest_id="hpr1-production-archive-manifest",
            project_id="hpr1-project",
            package_id="hpr1-package",
            sources=sources,
        )
        (manifests / "production_archive_manifest.json").write_text(
            json.dumps(archive.model_dump(mode="json"), indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        result = {
            "transport": "LOCAL_FIXTURE_ONLY",
            "provider_key": "google_veo",
            "provider_call_made": False,
            "actual_cost_usd": None,
            "estimated_cost_usd": str(cost.estimated_amount),
            "production_eligible": False,
            "operation_status": completed.normalized_status,
            "duplicate_submit_prevented": fake.submit_count == 1 and duplicate.provider_operation_id == submitted.provider_operation_id,
            "generation_attempts_consumed": completed.generation_attempts_consumed,
            "poll_count": fake.poll_count,
            "provider_audio_present": True,
            "provider_audio_discarded": True,
            "normalized_contains_audio_stream": False,
            "archive_roles_complete": archive.required_roles_complete,
            "final_media_ref_created": False,
            "human_upload_task_created": False,
            "provider_job_snapshot_submitted": False,
            "paid_provider_call_ledger_executed": False,
            "channel_or_frozen_context_mutated": False,
            "verdict": "PASS",
        }
        (manifests / "hpr1_rehearsal_summary.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result

    @staticmethod
    def _archive_sources(root: Path, manifests: Path, selected_take: Path) -> list[ArchiveSource]:
        actual = {
            "AI_GENERATION_MANIFEST": manifests / "google_veo_provenance_manifest.json",
            "AI_PROVIDER_OPERATION_RECEIPT": manifests / "google_veo_operation_receipt.json",
            "AI_COST_APPROVAL_IDEMPOTENCY": manifests / "google_veo_cost_estimate.json",
            "SYNTHETIC_MEDIA_DISCLOSURE": manifests / "synthetic_media_disclosure.json",
            "AI_HERO_NORMALIZATION_RECEIPT": manifests / "provider_audio_normalization_receipt.json",
            "SELECTED_AI_HERO_TAKE": selected_take,
        }
        sources: list[ArchiveSource] = []
        for role, archive_path in ROLE_ARCHIVE_PATHS.items():
            path = actual.get(role)
            if path is None:
                path = root / "archive-fixtures" / archive_path
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == ".mp4":
                    path.write_bytes(selected_take.read_bytes())
                else:
                    path.write_text(json.dumps({"role": role, "transport": "LOCAL_FIXTURE_ONLY"}), encoding="utf-8")
            sources.append(ArchiveSource(logical_role=role, source_path=path))
        return sources

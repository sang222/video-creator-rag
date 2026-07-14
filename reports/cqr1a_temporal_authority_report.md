# CQR1-A — Post-TTS Temporal Authority Foundation

Date: 2026-07-14. Scope: contracts, fixture-only parsing/alignment, deterministic compilation, strict renderer input boundary and read-only evidence. No provider, paid canary, production render, Drive or YouTube execution.

## Verdict

```text
CQR1A_EXISTING_ARTIFACT_MAPPING=PASS
CQR1A_SPOKEN_TEXT_NORMALIZATION=PASS
CQR1A_PROVIDER_TIMING_CONTRACT=PASS
CQR1A_FORCED_ALIGNMENT_CONTRACT=PASS
CQR1A_ALIGNMENT_RECONCILIATION=PASS
CQR1A_CANONICAL_MEDIA_TIMELINE=PASS
CQR1A_TEMPORAL_AUTHORITY_GATE=PASS
CQR1A_RENDERER_AUTHORITY_BOUNDARY=PASS
CQR1A_FIXTURE_REHEARSAL=PASS
CQR1A_PROVIDER_EXECUTION=DISABLED
CQR1A_DATABASE_MIGRATION=NOT_REQUIRED
CQR1A_FINAL=PASS
PROCEED_TO_CQR1_B=true
```

This authorizes only a separately requested CQR1-B task. CQR1-B, CQR1-C, CQR1-D, paid canary and CH1-FLEX were not started.

## Existing artifact mapping

The repository already had `VoiceTimelineSnapshot`, `CaptionTrackSnapshot`, `VisualPlanSnapshot`, `NativeRenderPlan`, `CompiledNativeRenderManifest` and generic `ArtifactVersion`. `VoiceTimelineSnapshot` is useful lineage but cannot be promoted to the repaired authority because its contract allows `timing_source=ESTIMATED` and its segments use estimated start/end/duration.

`CanonicalMediaTimeline` therefore extends the existing `narration_timeline` generic `ArtifactVersion` lineage; no duplicate artifact type or new table is needed. Legacy timeline/snapshot/plan artifacts remain readable and unchanged. The complete item-level mapping is in `reports/cqr1a_temporal_authority_inventory.json`.

## Parallel-authority findings

The primary defect was confirmed in code and PA1R evidence:

- final ElevenLabs audio: `24.102313s`;
- final render: `25.0s`;
- PA1R scene plan and FFmpeg command: fixed `0–7 / 7–13 / 13–21 / 21–25` plus `-t 25`;
- M6 captions and visual scenes copy `estimated_start_time`/`estimated_end_time`;
- M12.2 SRT timing divides word counts by a WPM assumption and clamps cue duration;
- legacy `NativeMotionCompiler` copies plan timing without external audio evidence.

Those paths remain historical. A new repaired plan must declare `CANONICAL_STRICT`; missing canonical evidence, hash/audio/scene mismatch, estimated scene source or parallel caption/timing input blocks.

## Text and normalization contract

Three authorities are explicit:

1. `EditorialScriptText`: approved semantic meaning.
2. `SpokenTextNormalized`: exact TTS input, including normalization version, editorial/spoken hashes, traceable operations, complete source/spoken spans, spoken tokens and pronunciation refs.
3. `DisplayCaptionText`: a viewer projection whose every token references `spoken_token_ids`; it cannot become an independent transcript.

`SpokenTextNormalizer` is deterministic, idempotent for the same input/policy, hashable, LLM-free and provider-free. Fixture coverage includes known abbreviation, acronym, integer/decimal, USD currency, ISO date, percentage, number range, URL-like token, whitespace and approved dictionary mapping. Ambiguous normalization, empty mapping/semantic deletion, source accounting gaps and unmapped inserted tokens block.

## Provider timing and forced alignment

The repaired ElevenLabs request contract targets `/v1/text-to-speech/{voice_id}/with-timestamps`, sends exact `SpokenTextNormalized.spoken_text`, sets provider text normalization off and carries model, voice settings, optional seed and versioned pronunciation dictionary locators. The parser converts `alignment` and `normalized_alignment` into provider-neutral `NarrationTimingSeed`. Raw base64 response data is never the runtime authority.

`NarrationAlignmentVerifier` exposes the provider-neutral forced-alignment boundary. The primary adapter builds multipart field names for `/v1/forced-alignment`; parser coverage includes the provider's word objects, character-object list and top-level `loss`. `FixtureOnlyAlignmentTransport` copies local JSON only and reports no network/provider call.

Current safe readiness:

```text
ELEVENLABS_TTS_CONFIGURED=false
ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=unknown
```

Before CQR1-D, an operator must grant the restricted key `Text to Speech: Access`, `Voices: Read`, `Models: Read`, and `Forced Alignment: Access`, then explicitly set the confirmation boolean. CQR1-A did not change permissions or inspect/log a secret value.

## Alignment reconciliation

`NarrationAlignmentReconciler` maps normalized provider characters and forced words onto stable spoken token IDs. Provider timing is the primary seed; forced alignment is required verification. It records whitelisted orthographic/tokenization differences, missing/extra tokens, timing conflicts and confidence. A conflict above 250 ms blocks.

PASS invariants are exact: token coverage `1.0`, no unexplained missing/extra token, monotonic word timing and all spans inside the measured final-audio duration. Provider timing alone and forced alignment alone both block; there is no estimate fallback.

## CanonicalMediaTimeline and scene compilation

The timeline includes all required project/package/channel, script/spoken/TTS/audio and timing-evidence refs; deterministic segments include editorial/spoken spans, verified words, phrase boundaries, audio spans and scene anchors. Caption layout, asset binding and visual scores remain nullable for CQR1-B/C.

Scene starts come from each segment's first verified word, scene audio ends from its last verified word, and the final scene may extend to the measured audio duration. Token overlap/gap, scene overlap, estimated timing, or final timeline/audio mismatch blocks. The workspace manifest is atomic JSON and the future archive role is `CANONICAL_MEDIA_TIMELINE` at `02-audio/canonical-media-timeline.json`.

## TemporalAuthorityGate and renderer boundary

`TemporalAuthorityGate` requires exactly one final audio asset, one spoken revision, one verified alignment and one canonical timeline. It validates spoken hash, provider/forced refs, token coverage, monotonicity, audio/timeline duration and scene timing provenance. All specified block reason families are exercised.

`NativeRenderPlan`, compiled manifest and FFmpeg command now propagate:

```text
canonical_media_timeline_ref
canonical_media_timeline_hash
canonical_audio_asset_ref
```

Strict compilation receives the referenced timeline object and verifies its own hash plus exact per-scene start/end/duration. The renderer also rejects a command whose authority refs differ from its compiled manifest. Historical plans default to `LEGACY_HISTORICAL`; this provides read compatibility only.

## Read-only evidence

Added:

```text
GET /video-projects/{project_id}/temporal-authority
GET /video-packages/{package_id}/canonical-media-timeline
```

The endpoints expose revisions, redacted audio ref, duration, timing/forced availability, coverage, timeline hash, gate state/reasons, safe readiness and exact next action. They expose no raw audio URL, credential or action endpoint.

## Fixture rehearsal

Evidence: `var/tmp/cqr1a-temporal-authority-fixture/`.

```text
rehearsal=CQR1A_LOCAL_FIXTURE_ONLY
TemporalAuthorityGate=PASS
token_coverage=1.0
provider_call_made=false
network_call_made=false
drive_call_made=false
youtube_call_made=false
production_render_made=false
fixture_is_real_provider_verification=false
```

Failure coverage: missing/extra word, missing provider timing, missing forced alignment, high timing conflict, non-monotonic word time, out-of-audio word, estimated scene, timeline/audio mismatch and parallel caption timeline.

No `FinalMediaRef`, `HumanUploadTask`, `UploadedVideo`, `ChannelProfileVersion`, frozen context, FormatIdentity, learning or prompt mutation was created.

## Network no-execution and historical immutability

The rehearsal ran with `socket.socket` replaced by a failing sentinel. Provider transports remain disabled and request contracts report `provider_call_made=false`. No Pexels, ElevenLabs, forced-alignment, Veo, Drive or YouTube transport executed.

Historical PA1R evidence hashes remain:

```text
pa1r_guarded_provider_smoke_report.md ebe6b0eafa6d1dc3c96d4182d4278f37ad9fa031a88c896fa3f0b00687977c74
pa1r_provider_smoke_human_review.md 20cc3a63798726119b2d74ebf4b2062ccc24b3526c4f433d55f1dc8cd2f0402c
pa1r_summary.json 1e274e1e0a6ad8bafe93f39cec210e8a7431fa145edc7c2e97851d965e3bfeb9
```

## Migration and regression

Migration decision: `NOT_REQUIRED`. Existing JSONB `ArtifactVersion.content` stores the new contract; Alembic remains one head at `0036_hpr1_veo`.

Verification:

```text
tests/test_cqr1a_temporal_authority.py: 29 passed
required focused regression suite: 147 passed, 2 dependency deprecation warnings
compileall: PASS
Alembic heads: 0036_hpr1_veo (one head)
git diff --check: PASS
frontend checks: NOT_RUN (no frontend changes)
```

## P0/P1/P2/P3

- P0: none.
- P1: none.
- P2: none open within CQR1-A; caption presentation/voice pace and visual continuity remain intentionally assigned to CQR1-B/C.
- P3: ElevenLabs TTS configuration is absent and forced-alignment permission confirmation is unknown; this does not block offline CQR1-A but must be resolved before CQR1-D paid canary.

Exact next action: start CQR1-B as a separate task, consuming `SpokenTextNormalized`, `VerifiedNarrationAlignment` and `CanonicalMediaTimeline`; do not call a provider or change canonical timing authority.

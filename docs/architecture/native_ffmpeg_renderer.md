# NativeFFmpegRenderer architecture

NR1 freezes the first-channel render path as `NativeRenderPlan → NativeMotionCompiler → CompiledNativeRenderManifest → FFmpegCommandManifest → NativeFFmpegRenderer → MediaQCReport → HumanReviewReceipt`.

The renderer is a local capability, not an external or paid provider. It accepts compiled manifests only, executes argv without a shell, writes `.part.mp4`, atomically renames on success, and creates no provider ledger, upload task, Drive state, YouTube state, CloudMediaRef, or production FinalMediaRef. Production execution defaults disabled. The renderer cannot infer narrative treatment, select assets, or accept raw filter syntax.

Active output profiles are 1080p30 landscape and portrait H.264 VideoToolbox, yuv420p/BT.709, AAC 48 kHz stereo and MP4 fast-start. VideoToolbox is recorded as target/max VBR; strict CBR is not claimed. 4K and ProRes remain inactive.

FormatIdentityContract and EpisodeOriginalityManifest refs/hashes are copied into the immutable plan and compiled evidence. The renderer cannot mutate them.

For Google Veo hero input, `MediaNormalizer` compiles `-an` and records whether provider audio existed and was discarded. MediaQC must prove the normalized hero has no audio stream. ElevenLabs is the narration authority; NativeFFmpeg is the final audio-mix authority.

## CQR1-A temporal boundary

New repaired execution uses `temporal_authority_mode=CANONICAL_STRICT`. `NativeRenderPlan`, `CompiledNativeRenderManifest` and `FFmpegCommandManifest` carry the same `canonical_media_timeline_ref`, timeline hash and final narration audio ref. `NativeMotionCompiler` validates the referenced timeline hash, audio match and exact per-scene start/end/duration before it emits a manifest. Independent caption timing, estimated scenes and parallel timing inputs block. FFmpeg receives compiled timing and does not reconstruct it from script word count or target duration.

`LEGACY_HISTORICAL` remains readable only for immutable NR1/NR2/AS1/PA1R evidence; it is not valid for a new repaired production path.

## CQR1 caption and creative boundary

For a caption-compiled strict timeline, `NativeMotionCompiler` embeds the cue
payload, spoken-token lineage, compilation hash, render-payload hash and frozen
libass style. Missing canonical cues and any independent filesystem SRT are
rejected; strict mode has no legacy caption fallback. `FFmpegCommandBuilder`
uses the same ASS builder as bbox preflight, with explicit canvas resolution and
policy-relative style values. Strict duration is the canonical final-narration
endpoint and must equal the final scene end; the command does not emit a
caller-controlled final `-t`.

The command manifest binds argv plus generated ASS/filtergraph SHA-256 values.
Execution recomputes the command hash and generated-file checksums before
FFmpeg, so an edited caption file, filtergraph or argv fails closed. The
synthetic fixture builder rejects `CQR1_CONTROLLED_PAID_CANARY`; paid canary
media must use approved narration and visual inputs, never synthetic sine/color
fixtures.

Provider-backed repaired scenes also carry VisualDirectionContract refs/hashes
and non-blocking semantic, continuity and adjacency gate evidence. The compiler
propagates these results but never selects, ranks or re-scores an asset.

Technical probing now measures ffprobe streams, complete `-xerror -map 0`
decode, A/V drift, checksum, Fast Start atom order and duration instead of
assuming them. Its CQR1 adapter emits a non-publishable `TechnicalMediaQC`
artifact. Creative and human watchability results remain separate artifacts; a
technical PASS cannot create a creative or human PASS.

## CH1-FLEX project binding

For new channel-scoped projects the plan must use the frozen `native_render_policy_snapshot_ref/hash`, `creative_quality_policy_ref/hash`, provider-use ref/hash and format-identity ref/hash copied from the project. A strict plan is not valid before final narration and `CanonicalMediaTimeline`. NativeFFmpeg remains a generic final renderer and never branches on channel or Strategy B.

## VSR1 exact-text boundary

New `VSR1_STRICT` scenes bind the preferred visual route and source-decision
ref/hash into `NativeRenderScene`. Exact text, numbers, logos and real UI are
never delegated to generated pixels. Normalized `TextSafeRegion` values and a
route-bound `NativeOverlayPlan` preserve authoritative content refs and reserve
space outside caption-safe regions. Missing route evidence or a required
overlay plan fails validation before compilation.

VSR1 defines this typed admission boundary only. It does not render media and
does not change the renderer's final-composition authority. Legacy plans remain
readable without route-aware fields; new strict plans cannot silently inherit a
historical Pexels choice.
# LPRO1 production authorization

Manifest `production_eligible=true` không còn bị từ chối vô điều kiện. `NativeFFmpegRenderer.authorize` yêu cầu `ProductionRenderExecutionEnvelope` exact plan ref/hash, operator approval, provider/cost policies và MR1 scoped approval. Việc authorize không tự bật execution; `VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED` vẫn là fail-closed runtime guard.

Fixture LPRO1 được phép riêng với purpose `LPRO1_OFFLINE_FIXTURE`, bắt buộc non-production và chạy actual-byte QC gồm caption, black-output và scene coverage.

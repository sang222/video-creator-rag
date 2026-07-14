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

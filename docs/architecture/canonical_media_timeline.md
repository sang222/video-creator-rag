# CanonicalMediaTimeline architecture

## Authority chain

The repaired path is strictly one-way:

```text
approved EditorialScriptText
  -> deterministic SpokenTextNormalized
  -> ElevenLabs convert-with-timestamps response (seed only)
  -> ElevenLabs Forced Alignment response (verification only)
  -> VerifiedNarrationAlignment
  -> CanonicalMediaTimeline
  -> caption/scene/asset timing projections
  -> NativeMotionCompiler -> NativeFFmpegRenderer
```

Final narration audio plus verified word timing is the temporal authority. Provider character timing is never consumed directly by captions, scene planning or rendering. Forced alignment is mandatory in `CANONICAL_STRICT`; it is not an optional fallback. Word-count, script-duration, fixed-scene and renderer-reconstructed timing are forbidden in this mode.

## Text authorities

- `EditorialScriptText` owns approved meaning and display-oriented notation.
- `SpokenTextNormalized` owns the exact TTS input. Version, hashes, operations, source/spoken spans, spoken tokens and pronunciation-dictionary refs are immutable and deterministic.
- `DisplayCaptionText` is only a projection. Every display token references one or more spoken token IDs; it cannot become a second transcript.

`SpokenTextNormalizer` is English-locale, deterministic, provider-free and LLM-free. It supports approved abbreviation/acronym, cardinal/decimal/range, ISO date, USD currency, percentage, URL and pronunciation-dictionary rules. Ambiguous rules, empty mappings, semantic deletion, unmapped insertion and incomplete source accounting block.

## Timing evidence and reconciliation

`NarrationTimingSeed` is the provider-neutral parse of ElevenLabs timestamp output. It records redacted request metadata, final audio ref/duration, source/spoken hashes, original and normalized character spans, model, voice, seed/settings and dictionary refs. Raw base64/audio and credentials are not durable authority.

`ForcedAlignmentEvidence` is parsed through `NarrationAlignmentVerifier` semantics. Words must be monotonic, in audio bounds and mapped to known spoken tokens. Missing/extra words block unless the difference is a whitelisted orthographic/tokenization normalization.

`NarrationAlignmentReconciler` maps provider normalized characters and forced-alignment words to spoken tokens. Provider spans remain the primary seed; forced alignment verifies them. Conflicts are recorded deterministically. More than 250 ms discrepancy blocks. PASS requires exactly 100% spoken-token coverage, no unexplained missing/extra token and valid final-audio bounds.

## CanonicalMediaTimeline

Top-level lineage includes project/package/channel, editorial and spoken revisions, TTS request, final audio and duration, all three timing evidence refs, segments, QC metrics, warnings and deterministic hash. Each segment contains editorial/spoken spans, spoken token IDs, verified words, phrase boundaries, audio spans and scene anchors. Caption layout and asset selection remain nullable for CQR1-B/C.

Scene anchors are compiled only from verified word/phrase spans. The last scene may extend from its last verified word to the measured final-audio duration. It may not begin before its first spoken token, end before its final token, overlap another scene or use estimated timing.

## Persistence and archive

No dedicated table or duplicate artifact type is needed. The existing `narration_timeline` `Artifact`/`ArtifactVersion` lineage is extended with the versioned `CanonicalMediaTimeline` JSON. The same JSON is stored as `manifests/canonical_media_timeline.json`. New repaired archives use logical role `CANONICAL_MEDIA_TIMELINE` at `02-audio/canonical-media-timeline.json`; the legacy required-role set is unchanged so historical AS1/PA1R evidence is not rewritten.

## Renderer boundary

New repaired plans set `temporal_authority_mode=CANONICAL_STRICT` and must carry:

```text
canonical_media_timeline_ref
canonical_media_timeline_hash
canonical_audio_asset_ref
scene_timing_source=CANONICAL_MEDIA_TIMELINE
caption_timing_source=CANONICAL_MEDIA_TIMELINE
parallel_timing_inputs=[]
```

The compiler receives the referenced timeline evidence and validates its content hash, audio ref and every scene start/end/duration. The compiled manifest and command copy the same authority binding. Hash mismatch, audio mismatch, estimate use, absent evidence or conflicting timing input blocks before FFmpeg.

Legacy plans remain readable as `LEGACY_HISTORICAL`. This is evidence compatibility only and does not authorize a new repaired production render.

Provider shapes are based on the official ElevenLabs [speech-with-timing](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps) and [forced-alignment](https://elevenlabs.io/docs/api-reference/forced-alignment/create/) references. The adapter maps the provider's top-level forced-alignment `loss` into provider-neutral `alignment_loss`; `transcript_loss` remains nullable when the provider does not return it.

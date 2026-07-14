# Caption and voice quality architecture

## Authority and policy

The repaired path keeps one temporal authority:

```text
SpokenTextNormalized
  -> final narration audio
  -> VerifiedNarrationAlignment
  -> CanonicalMediaTimeline
  -> canonical caption cues
  -> generated ASS
  -> NativeFFmpegRenderer
```

Caption cues never import timing from an independent transcript or SRT. Every
display token maps to one or more `spoken_token_ids`; ordered coverage must be
exactly `1.0`. Caption compilation changes the deterministic timeline hash.
Legacy estimated caption tracks remain readable only as historical evidence.

Presentation thresholds come from the versioned channel creative-policy
snapshot. Services receive the policy; they do not contain a channel name or
channel-specific constants.

## Narration pacing

`NarrationPacingAnalyzer` consumes verified words, measured audio duration,
detected silence spans and section boundaries. It reports:

- active-speech WPM, excluding policy-qualified silence;
- delivered WPM over full elapsed audio;
- active WPM inside the first eight seconds;
- median comma, sentence and section pauses;
- word-count, pause-span and waveform-summary evidence.

`NarrationPacingGate` returns `PASS`, `REVIEW_REQUIRED` or `BLOCK`. Fast body,
hook or delivered pace above block thresholds is a hard block. Mild slow pace
is reviewable; extreme slow pace blocks. Missing required pause evidence does
not become an invented zero-length pause.

`NarrationPacingCorrectionPlanner` enforces the normal correction order after
measurement. It accepts a measured PASS; permits at most one modest
provider-speed regeneration only when the current model supports it and that
provider attempt is explicitly authorized; otherwise it returns a blocking
recommendation. Dense text, short punctuation pauses and hard pace failure
produce `SCRIPT_PACING_REWRITE_REQUIRED`. FFmpeg `atempo` is emergency-only:
more than 2% requires human approval and more than 3% blocks. Every correction
requires remeasurement, and `atempo` cannot conceal punctuation, pause or
density defects.

## Caption compilation and geometry

`ReadableCaptionCompiler` groups only verified spoken tokens, prefers sentence,
clause and phrase boundaries, emits explicit line breaks and caps cues at two
lines. Names, modifier/head pairs and prepositional phrases are protected where
the deterministic phrase evidence identifies them. Allowed display changes are
limited to approved casing, punctuation simplification, number re-compaction
and branded casing.

The canonical cue stores token lineage, start/end time, lines, reading metrics,
bounds evidence and gate results. Segment-level aggregates retain cue IDs,
caption spans, token IDs, reading metrics and layout evidence.

`CaptionBoundsPreflight` writes an ASS document with explicit `PlayResX` and
`PlayResY`. Its transparent lavfi source retains RGBA before libass so
`alphaextract,bbox` cannot mistake an opaque background for caption pixels.
Font size derives from `min(frame_width, frame_height)`; the safe-margin floor
derives from frame height. Both use relative policy values, and `MarginV` also
accounts for the frozen outline/shadow/antialias footprint so the actual bbox
clears that floor. The same
resolved style snapshot (font, colours, border, outline, shadow, alignment and
aspect-specific margins) and the same FFmpeg/libass document builder are used
by preflight and final render. `alphaextract` and `bbox` measure the actual
non-empty pixels. Character count is useful evidence, but never substitutes for
measured geometry. ASS control sequences, embedded newlines and unsafe style
fields block before document generation.

Layout blocks on more than two lines, actual overflow, excessive block width,
text outside the frame or reading speed above the hard limit. Safe-area blocks
on an unsafe bottom margin or a required subject-zone overlap.

## Synchronization and render boundary

Expected cue start/end values are the first and last verified word spans for
the cue tokens. The sync, coverage and drift gates separately enforce offset
thresholds, exact ordered token coverage, monotonic cues, no unexpected overlap,
audio bounds and final endpoint consistency.

In `CANONICAL_STRICT`, `NativeMotionCompiler` embeds canonical cue payloads and
their compilation and render-payload hashes in the compiled manifest. It
rejects a missing cue set and never falls back to an independent SRT. The
command builder generates ASS from that schedule, binds the ASS/filtergraph
checksums into the command hash, and derives duration from the final narration
endpoint. It does not add an independent final `-t`.

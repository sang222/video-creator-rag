# AS1 Asset Acquisition and Provenance

AS1 adds a deterministic, script-driven boundary between `NativeRenderPlan` and future asset resolution. It is validation/planning only: no provider client, Drive client, paid ledger, render job, final media reference, upload task, or network execution is created.

## Canonical flow

```text
NativeRenderPlan + approved FormatIdentity + channel-scoped visual strategy
  -> AssetRequestCompiler
  -> CompiledAssetRequestPlan
  -> native request | PexelsQueryPlan | AIHeroAssetRequest
  -> fixture-only parse/rank/rendition/download evidence
  -> provenance + normalization plan
  -> ProductionArchiveManifest
  -> archive verification
  -> local purge eligibility
```

Asset priority is frozen as `NATIVE_VISUAL`, then `SUPPORTING_STOCK`, then `AI_HERO`. Strategy B is accepted only for `small-team-ai`. Native visual treatment remains semantic truth; the renderer cannot select or reinterpret assets.

## Compiler policy gates

`AssetRequestCompiler` blocks an unapproved or contradictory FormatIdentity snapshot, non-`NO_CHARACTER` policy, Strategy B outside its channel, unsupported provider intent, a role not allowed by FormatIdentity, or any AS1 provider-execution flag. Each request retains source segments, semantic visual intent, duration/resolution/crop/person/logo/evidence policy, deterministic fallback order and content hash.

For new repaired paths, request duration is a projection of `CanonicalMediaTimeline` scene anchors copied through a `CANONICAL_STRICT` `NativeRenderPlan`. Asset acquisition cannot introduce a fixed or word-count scene duration. Final asset binding, semantic ranking and continuity scoring remain CQR1-C scope.

Stock cannot be factual evidence, a testimonial, an endorsement, or a recurring host. AI hero requests remain provider-neutral and require `HOOK`, `METAPHOR`, `EMOTIONAL_PAYOFF`, `VISUAL_SIGNATURE`, or `NATIVE_MOTION_INSUFFICIENT`.

## Provider adapter boundary

Pexels support consists only of a structured request builder, response parser, deterministic metadata ranker, compatible MP4 rendition selector, redacted download-plan builder, and rate-limit metadata parser. The endpoint is fixed to `/v1/videos/search`; query params stay structured. Durable evidence stores a `volatile://` reference derived from the raw link, never a tokenized URL or API key.

Ranking uses semantic metadata plus composition, resolution, duration, crop safety, logo/text, person, brand, prior use, motion, channel-identity fit and source completeness. It is deterministic and always leaves final selection at a human-review boundary; no CV/vector claim is made.

`AIHeroAssetRequest` and `AIGenerationManifest` contain no provider-specific visual-role semantics. Frozen policy resolves an approved `AI_HERO` request to a provider-specific `GoogleVeoGenerationRequest`. Planned manifests have null operation ID, submit/completion timestamps, output reference, actual cost, attempt and QC refs; `production_eligible=false` is invariant.

## Local and archive safety

`LocalProjectWorkspaceService` confines every path beneath `VCOS_LOCAL_PROJECT_WORKSPACE_ROOT`, owns one directory per project, rejects traversal/symlink escape, preflights disk space, enforces file-size limits, streams SHA-256 through `.part`, fsyncs, then atomically renames. Success state requires an existing file and matching checksum.

`MediaNormalizer` compiles sanitized argv only. Video plans freeze canvas/fps/timebase/yuv420p/BT.709/trim/audio policy. Audio plans freeze 48 kHz stereo, loudness policy reference and duration alignment. `execution_allowed=false`.

`ProductionArchiveManifest` records required logical roles, archive paths, sizes, SHA-256/MD5 and purge requirements. Rejected stock, normalized temporary media, scene scratch, cache and unusable failed generations are excluded by default.

Drive metadata verification is fixture-only. The configured Drive folder is already the root. Valid relative production paths are:

```text
company_{company_id}/channel_{channel_workspace_id}/project_{video_project_id}/production-package-v1
```

Nested `VCOS` and unknown scope segments block. One required mismatch sets archive state `FAILED`; local purge remains ineligible. Only a `VERIFIED` receipt can advance to purge eligibility.

## Read-only surface

- `GET /video-packages/{package_id}/asset-acquisition-plan`
- `GET /video-projects/{project_id}/local-workspace-summary`
- `GET /video-projects/{project_id}/archive-readiness`

These routes expose evidence, counts, readiness and the exact next action. They add no search, download, generation, archive, purge, render or upload action.

## VSR1 routing evidence

For a new strict visual plan, acquisition is downstream of a deterministic
`VisualSourceDecision`; it cannot choose a source by trying providers. Planning
evidence may carry `preferred_source_route`, nullable `actual_source_route`,
routing reason codes, fallback class, source-decision ref/hash, eligibility-gate
refs, evidence-truth classification and `native_overlay_required`.

`actual_source_route` stays null in VSR1 because no asset is acquired or
generated. Pexels result quality is not a routing input and a failed search does
not open an AI-image route. AI-image and Veo routes express future acquisition
requirements only; provider execution, response URLs, attempts, cost events and
output receipts are absent until a separately approved milestone.

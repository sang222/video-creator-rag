# M7 Manual Publish Handoff

## Scope

M7 turns an M6 `RenderPackageSnapshot` into a manual publish handoff for a human operator, then captures the human-entered actual publication result after upload outside VCOS.

Included:

- `PublishHandoffPackage` with planned files, planned metadata, planned disclosures, checklist, operator instructions, risk summary, and exact M6 lineage.
- Platform-specific checklist and plain-language instructions.
- `ManualPublishConfirmation` with actual platform id, URL, published time, actual metadata, actual files, actual disclosures, validation summary, and planned-vs-actual diff.
- `UploadedVideo` as the durable M8 analytics anchor.
- Metrics-free `UploadedVideoPublicationSummary` for future operator/dashboard surfaces.
- Audit/domain events for handoff, confirmation, uploaded video, metadata diff, disclosure review, and ready-for-analytics state.

## Non-Scope

M7 does not upload or publish anything. It does not implement platform APIs, OAuth upload flows, scheduled uploads, analytics sync, analytics snapshots, no-view diagnostics, recovery services, memory promotion, dashboard UI, real provider integrations, source scraping/parser, vector/RAG, OPA/Cedar, Algorithm/Growth/View agents, fake traffic, bot engagement, platform evasion, IP/VPS tricks, or auto-reupload.

## Lifecycle

1. M6 creates a render package with final video ref, caption ref, checksum manifest, RenderSpec, MediaQC, AccessibilityQC, SourceManifest, AssetManifest, and policy snapshot lineage.
2. `PublishHandoffService.create_from_render_package` creates a `PublishHandoffPackage`.
3. Operator reviews checklist and instructions.
4. `mark_ready` moves the handoff to `READY_FOR_OPERATOR` when required file refs and MediaQC are acceptable.
5. Human uploads manually outside VCOS.
6. Human enters actual `video_id`, `video_url`, `published_at`, metadata, files, and disclosure/license confirmations.
7. `ManualPublishConfirmationService` validates actual data and computes planned-vs-actual metadata diff.
8. `accept_confirmation` creates `UploadedVideo` and publication summary only when required disclosure/license confirmations pass.

## Contracts

`PublishHandoffPackage` links exact `video_project_id`, `render_package_snapshot_id`, `policy_snapshot_id`, `render_spec_snapshot_id`, MediaQC, AccessibilityQC, SourceManifest, and AssetManifest refs where available. It stores planned metadata only; actual metadata belongs to `ManualPublishConfirmation` and `UploadedVideo`.

`ManualPublishConfirmation` requires syntactically valid actual video id, URL, published time, actual metadata, and actual disclosure/license confirmations. Missing required AI disclosure or rights confirmation returns `REVIEW_REQUIRED`; it does not create `UploadedVideo`.

`UploadedVideo` preserves VideoProject, RenderPackage, PolicySnapshot, SourceManifest/RightsEnvelope, actual metadata, actual disclosures, and lineage refs. `monitoring_state=READY_FOR_ANALYTICS` means only that M8 has a valid external id/URL anchor later; M7 stores no metrics.

## Planned Vs Actual Diff

M7 records changed title, description, tags, thumbnail, privacy status, and disclosure state. Low severity metadata changes can still be accepted. Thumbnail/privacy changes are medium review signals. Missing required AI disclosure or rights confirmation is high severity and review-required.

## Operator Policy

`vcos publish` means manual handoff/confirmation only. It never uploads, calls a platform publish API, or obtains platform credentials. The operator must copy actual platform values back into VCOS after manual upload.

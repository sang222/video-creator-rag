# YouTube PRIVATE staging and human PUBLIC cutover

The active long-form lane is:

`QC PASS -> PRIVATE staging -> PRIVATE_VERIFIED -> human Studio review -> human PUBLIC or reject/needs-rerender -> read-only PUBLIC observation -> PUBLICATION_VERIFIED`.

`PRIVATE_VERIFIED` is deliberately not `PUBLICATION_VERIFIED`. Until the latter exists, VCOS must not create `UploadedVideo`, schedule analytics, allocate a public series ordinal, or create learning evidence.

## Authorities

- A `FinalReviewCandidate` is the frozen package/QC/archive authority.
- `YouTubePrivateStage` is candidate-bound in the active lane and does not require `FinalVideoDecision`.
- Automated provider writes are limited to the official YouTube Data API `videos.insert` resumable body with `privacyStatus=private`, thumbnail, and caption effects.
- The credential is channel-bound and explicitly has no public-release or delete capability.
- `YouTubePrivateReworkRequest` records a human reject or needs-rerender disposition. The old stage and its provider identity remain historical; VCOS never deletes or unpublishes it.
- `PublicPublicationReceipt` is created only from the exact staged video/channel identity and an observed provider `PUBLIC` readback. Legacy decision/confirmation links remain nullable compatibility fields.

## Truthful evidence

The local thumbnail/caption effect hash and the provider component receipt are separate from the public observer’s claim. VCOS records assurance plus `exact_remote_bytes_unavailable`; it does not claim exact remote thumbnail bytes. Self-declared audience settings and effective provider readback are separate fields. Synthetic-media assessment is versioned, hash-bound to the disclosure snapshot, and carried through the receipt lineage. Human full-watch is an operator procedure/attestation, not an automated fact.

There is no active Drive dependency: the active candidate must reference checksum-verified local/archive bytes. Drive remains readable only for historical compatibility.

## Durable human wait

When the exact staged video and channel read back successfully but remain
`PRIVATE`, the observer records no error and returns a dedicated
`WAITING_FOR_HUMAN_PUBLIC` settlement. The observation event stays pending,
its lease is cleared, and `next_attempt_at` is scheduled at the existing
delivery backoff cap. `attempt_count` remains the truthful number of event
executions; `human_wait_count` records expected waiting cycles separately.

Technical readback failures use a separate `technical_failure_count` and keep
the existing bounded retry/dead-letter policy. Human delay alone therefore
cannot exhaust that budget or dead-letter the observer, while rejected,
blocked, stale, or superseded stages remain ineligible for publication.

## Operator surface

The cockpit exposes the actual staged Studio URL and the next action to review it. It does not render the legacy UPLOAD/DO_NOT_UPLOAD decision or manual publication-confirmation form for the active lane. The only public action is the human’s action in YouTube Studio. VCOS then observes and reconciles the result.

# M10.2 Media Provider Role Matrix

The active VCOS production surface supports YouTube long-form only. Provider
roles are channel-scoped planning authority; they do not duplicate the channel
duration contract and they never grant upload or publish authority.

## Provider boundaries

| Provider role | Authority |
| --- | --- |
| VCOS backend | Orchestration, state, budget, manifests, QC, final-decision handoff |
| LLMRouter | Guarded research, planning, script, metadata, and review language tasks |
| TTS provider | Long-form narration only |
| Caption timeline service | Caption timing and track planning |
| AI visual provider | Optional bounded scene assets; never final assembly |
| NativeFFmpegRenderer | Canonical local 1920x1080 H264/AAC assembly |
| Archive client | Deterministic archive journal, readback, and checksum verification |
| MediaQC | Technical and automated creative QC |
| Publish handoff | Manual upload task after an exact `UPLOAD` decision |

Paid providers are guarded by explicit capability, budget, license, and routing
policy. A routing decision is planning evidence only. It cannot prove that
media exists, substitute providers, or bypass the local final-render boundary.

## Active readiness sequence

```text
ProductionPackage
→ automated readiness
→ narration/alignment
→ CanonicalMediaTimeline
→ resolved assets
→ NativeRenderPlan
→ NativeFFmpeg
→ technical QC PASS
→ creative automated QC PASS
→ archive VERIFIED
→ FinalMediaRef
→ FinalReviewCandidate
→ human UPLOAD | DO_NOT_UPLOAD
```

There is no pre-render script or package approval in the active v2 sequence.
Legacy package-review artifacts remain readable historical evidence but cannot
become current launch, cadence, render, or publish authority.

`FinalMediaRef` requires exact file bytes, SHA-256 lineage, duration compliance,
audio/video streams, QC evidence, and verified archive evidence. Creating it
does not create an upload task. Only the exact final-video `UPLOAD` decision may
create the canonical manual task; VCOS never auto-publishes.

## Gates

- Capability gates reject unregistered jobs and provider substitution.
- Budget gates use configured caps and supplied estimates only.
- License gates block unverified external assets.
- Reuse gates require an active long-form consumer and exact rights lineage.
- Media QC blocks missing files, bad duration/aspect ratio, missing audio,
  unreadable captions, and corrupt frames.
- Analytics remains read-only publication learning authority.

## Deferred

Phase E owns full analytics scheduling and launch-performance read models.
Provider execution remains disabled unless an explicit future execution task
authorizes it.

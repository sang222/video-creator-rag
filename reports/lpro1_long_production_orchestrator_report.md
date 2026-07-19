# LPRO1 Long Production Orchestrator Report

Updated: 2026-07-19

## Verdict

`LPRO1_FINAL=PASS`

LPRO1 now owns the controlled transition from an exact, human-approved scripted package to a non-publishable local review candidate. It does not activate MR1, call a provider, upload to Drive or YouTube, or create a production `FinalMediaRef`.

## Entry Evidence and Repository Mapping

- Entry branch: `main`.
- Entry commit: `389fe8d857d8110a1c85b5fcb0da58826c03c939`.
- Entry gate: `PASS`; MR1 was and remains `ON_HOLD`.
- The full producer/consumer/persistence/decision mapping is recorded in `reports/lpro1_repo_inventory.json`.
- Existing D2P1, M12.2, temporal-authority, VSR1, asset-acquisition, native-renderer, QC, review, R3D8 cost and upload domains were extended or reused. No parallel second render-plan domain was introduced.

## Repaired State Machine and Runtime Wiring

The application now exposes a controlled Daily-to-package trigger at `POST /daily-idea-decisions/{decision_id}/production-handoff/run`. It resolves the admitted decision and frozen lineage, invokes `DailyToPackageOrchestrator.run()`, resumes by idempotency key, and does not accept manual topic/category/pillar overrides or auto-render.

An exact completed `ReviewTask` plus matching `ApprovalDecision=PASS` promotes the scripted package through `PACKAGE_HUMAN_REVIEW_PASSED` to `READY_FOR_LONG_PRODUCTION`. The transition is durable and tied to the reviewed package version. Package existence alone remains `PACKAGE_READY_FOR_HUMAN_REVIEW`.

LPRO1 is wired at `POST /video-projects/{project_id}/long-production/run`, with a read-only status projection at `GET /video-projects/{project_id}/long-production`. Request bodies carry only package identity, mode and the production-execution envelope; callers cannot replace frozen authority fields.

The state distinctions are preserved:

- scripted package exists is not video created;
- package review-ready is not final-render-ready;
- long-production-ready is not an MP4;
- native plan ready is not render completed;
- human-review-ready requires an actual MP4 and TechnicalMediaQC PASS;
- upload-ready requires exact media bytes, checksum and human PASS.

## M5 and Upload Readiness Repairs

Daily `run_mode` authority is consistent across contract, ORM, service, fixtures and database: `MOCK`, `REAL_DISABLED`, and `REAL`. The default remains `REAL`. `MOCK` executes deterministic offline logic, `REAL_DISABLED` fails closed before LLM execution, and `REAL` is schema-valid but fails closed unless the existing router, registered lane/model, prompt registry, prompt budget, cost and approval gates are ready.

Forward migration `0038_lpro1_daily_mode` replaces the two relevant checks without editing migration 0006. It also permits `UPLOAD_INPUT_MISSING` and `AWAITING_FINAL_MEDIA`, so an upload card with `file_ref=None` cannot be projected as ready. A fresh upgrade and a 0037 -> head -> 0037 -> head round-trip passed with one Alembic head.

## Strict Render Package

The strict `LongFormRenderPackage` requires frozen project/package/channel/profile/policy/niche authorities and hashes; approved script and exact human-review authority; narration request/result and audio checksum; verified final-audio alignment; canonical timeline and caption track; visual direction; a source decision and resolved normalized asset for every scene; rights/provenance evidence; native render plan and policy; provider execution plan and cost estimate; renderer eligibility; approval and idempotency references.

Missing, stale, unresolved or mismatched authorities fail closed. Legacy M10.2 packages remain readable, but are projected truthfully as routed/awaiting inputs and cannot claim strict final-render readiness.

## Orchestrator, Timeline and Asset Flow

`LongProductionOrchestrator` sequences one owner across:

1. reviewed D2P/M12.2 package resolution;
2. narration request/result and local fixture WAV;
3. provider-timing seed plus deterministic forced alignment;
4. `VerifiedNarrationAlignment` with full spoken-token coverage;
5. `CanonicalMediaTimeline`, which is the sole scene/caption/audio timing authority;
6. readable caption compilation from that timeline;
7. per-scene `VisualSourceDecision` binding;
8. local asset resolution, rights/provenance evidence and normalization;
9. strict package assembly;
10. `LongFormRenderPackageToNativeRenderPlanAdapter`;
11. existing `NativeMotionCompiler`;
12. typed FFmpeg authorization, local render and actual-byte QC;
13. non-production `ReviewMediaCandidate` creation.

The fixture exercised three distinct routes: `NATIVE_DIAGRAM`, `PEXELS_VIDEO` using a local stock-like test fixture, and `AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY` using a local generated-like still. All normalized assets are checksum-bound at 1920x1080. No network or provider acquisition occurred.

## Production Execution Envelope and Native Render

The former blanket rejection of every `production_eligible` manifest was replaced with a typed, hash-bound `ProductionExecutionEnvelope`. It binds package, plan, policy, provider-plan, cost, approval and idempotency authorities. It authorizes the executor boundary but never bypasses the independent production execution flag or MR1 gate.

`REAL_PRODUCTION` remains gated and was verified to stop before rendering without the exact MR1 envelope. `OFFLINE_FIXTURE` is explicitly non-production and used the existing native compiler plus real local FFmpeg.

Fixture output:

- Path: `/Users/sangss/Desktop/video-creator-rag/artifacts/lpro1/runs/df895a9e-c403-5d3d-9745-4b9918e253f5/lpro1-review-candidate.mp4`
- SHA-256: `113a4d8c8fd84e800d84fcbebc25ad6ef4df2988b958ff6650469b6b3e41f157`
- Size: `5,016,875` bytes
- Duration: `12.000000` seconds
- Video: H.264, 1920x1080, yuv420p, 30 fps
- Audio: AAC, 48 kHz, stereo
- FFmpeg exit code: `0`

## QC, Review Candidate and FinalMediaRef Boundary

TechnicalMediaQC ran against actual output bytes and passed decode, codec/container, stream integrity, dimensions, FPS, audio format, duration, fast-start, checksum, non-black output, caption presence and scene coverage.

CreativePerceptualMediaQC is separate. It returned `REVIEW_REQUIRED` with `LPRO1_FIXTURE_HUMAN_WATCH_REQUIRED`; technical PASS was not promoted to creative or human PASS. The resulting `ReviewMediaCandidate` is `PENDING`, `production_eligible=false`, `not_publishable=true`, and checksum-bound to the MP4 and both QC receipts.

`FinalMediaCloseoutService` rejects absent media, checksum mismatch, technical failure, creative failure/review, missing exact human PASS, stale package/plan lineage, missing rights/provenance, missing archive verification and reuse of a review-only/non-production candidate. A valid isolated closeout contract was also tested. The LPRO1 fixture creates zero production `FinalMediaRef` rows.

## Idempotency, Resume and Failure Semantics

The orchestration run key is derived deterministically from the immutable package and execution inputs. Every stage writes versioned, checksum-addressed receipts; completed valid stages are resumed, while corrupt or incomplete evidence is not silently accepted. Repeated API execution returns the same candidate/run and does not duplicate project, research assignment, package, render or review candidate.

Schema-invalid requests and policy failures remain distinct from retryable technical failures. Read-only status calls never invoke providers or mutate execution state.

## Verification and Zero-Call Proof

- Original required LPRO1 suite: `160 passed`.
- Amendment/runtime/migration suite: `59 passed, 15 historical skips`, zero failures.
- Focused stale migration repair: `2 passed`.
- Fresh migration upgrade: `PASS`.
- Existing database forward upgrade and expanded constraints: `PASS`.
- Migration downgrade/upgrade round-trip: `PASS`.
- Execution receipt: `local_only=true`, `no_provider_calls_confirmed=true`, `production_eligible=false`.
- Provider calls: `0`.
- Drive calls: `0`.
- YouTube calls: `0`.
- Production FinalMediaRef created: `0`.

All repair attempts and terminal evidence are enumerated in `reports/lpro1_repair_cycles.json` and `reports/lpro1_summary.json`.

## Exact Next Action

`PROCEED_TO_PKG1_VISUAL_REVISION=true`

Do not start MR1. A later exact-target MR1 approval must cover the revised package, visual-source set, provider/cost plan and production execution envelope.

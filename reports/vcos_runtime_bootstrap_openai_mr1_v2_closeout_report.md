# VCOS runtime bootstrap, OpenAI cutover, and MR1 v2 closeout

**Closeout state:** `BLOCKED_EXTERNAL` at `OPENAI_CANARY`.

The local implementation, database lineage, policy bootstrap, source scans, and offline qualifications are complete. Live MR1 v2 execution correctly did not start: the configured OpenAI credential was rejected by the provider. No media provider, Drive upload, YouTube upload, final-media record, or final-decision record was fabricated.

## 1. Repository and migration state

| Item | Before / observed checkpoint | Closeout state |
| --- | --- | --- |
| Branch | `main` | `main` |
| Tracked HEAD | `b3744f35a93f274a66ed056cbb2a182bbc421115` | unchanged; no commit, tag, or push |
| Worktree | Intentionally dirty | preserved; no reset, stash, discard, or history rewrite |
| Historical expected migration | `0050_vcos_long_form_analytics` | actual pre-cutover database/source lineage had already reached `0051_openai_luna_terra_cutover` |
| Alembic head | `0051_openai_luna_terra_cutover` | `0052_vcos_strategic_lineage (head)` |
| Database current revision | migrated through the cutover authority | `0052_vcos_strategic_lineage (head)` |

Migration `0052_vcos_strategic_lineage` is the sole head. It has an explicit fail-closed downgrade guard (`DOWNGRADE_BLOCKED_STRATEGIC_LINEAGE_EXISTS`) once new lineage exists. The real configured database completed `0052 -> 0051 -> 0052`; the checked data had no new lineage rows before that guarded downgrade. Empty-database upgrade, configured-database upgrade, and offline SQL generation passed. Offline SQL ends with the `0051 -> 0052` version update.

At report creation, the intentional worktree consists of 91 tracked changed/deleted paths and 14 pre-existing/untracked paths; this report is the additional untracked closeout artifact. The checkout-wide list includes work that predated this activation as well as this task, so it is documented and preserved rather than attributed wholesale or cleaned up.

### Changed-path manifest

Core source and migrations:

- `alembic/versions/0050_vcos_long_form_analytics.py`
- `alembic/versions/0051_openai_luna_terra_cutover.py`
- `alembic/versions/0052_vcos_strategic_lineage.py`
- `app/api/routes/serializers_publish_learning.py`, `app/cli/main.py`, `app/core/config.py`
- `app/contracts/__init__.py`, `channel_policy.py`, `launch_cadence.py`, `long_form_analytics.py`, `m12_2.py`, `m5.py`, `m9.py`, `operator_cockpit.py`, `policy_snapshot.py`, `production_package.py`, `profile.py`, `vcos_v2.py`, `workflow.py`
- `app/db/models/__init__.py`, `launch_cadence.py`, `long_form_analytics.py`, `m10_1.py`, `m5.py`, `m9.py`, `workflow.py`, `openai_cutover.py`
- `app/providers/ollama.py` (deleted); `app/providers/openai.py` (added)
- `app/services/__init__.py`, `channel_contract.py`, `editorial_research.py`, `long_form_analytics.py`, `m10.py`, `m10_1.py`, `m11.py`, `m12.py`, `m12_2.py`, `m9.py`, `operator_cockpit.py`, `ops.py`, `production_package.py`, `production_publish.py`, `profile_compiler.py`, `r3d3.py`, `v2_package_readiness.py`, `v2_provider_production.py`, `v2_support_authority.py`, `vcos_v2.py`, `workflow.py`
- `app/services/openai_cutover.py`, `runtime_bootstrap.py`, and `v2_drive_archive.py` (added)

Runtime/configuration/documentation:

- `.env.example`, `Makefile`, `README.md`, `docker-compose.yml`
- `config/artifact_type_registry.yaml`, `llm_model_profile_catalog.yaml`, `llm_router_lane_catalog.yaml`, `m10_1_reason_code_catalog.yaml`, `m10_reason_code_catalog.yaml`, `provider_registry_catalog.yaml`
- `docs/architecture/architecture-ledger.md`, `m10-1-llm-router.md`, `m12-1-prompt-registry.md`, `m12-production-credential-readiness.md`, `source-of-truth.md`

Frontend:

- `frontend/src/features/integrations/integrations-readiness-view.tsx` and its test
- `frontend/src/features/launch/launch-cadence-dashboard.test.tsx`
- `frontend/src/features/production/final-review-surface.tsx` and its test
- `frontend/src/lib/types.ts`

Qualification and regression tests:

- `tests/qualification/conftest.py`, `helpers/qualification_asserts.py`, `helpers/repo_scanners.py`
- `tests/qualification/test_m10_learning_review_queue.py`, `test_m12_1_prompt_registry.py`, `test_m12_2_first_scripted_video_package.py`, `test_m12_provider_readiness.py`, `test_pre_m7_m4_ops.py`, `test_pre_m7_migrations.py`, `test_pre_m7_repo_preflight.py`, `test_r3d3_agent_context_pack.py`
- `tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py` (deleted), `test_m12_2s_full_agent_openai_rehearsal.py` (added)
- `tests/test_ch1_market_profile_v3.py`, `test_cli.py`, `test_m12_1r_mock_runtime_purge.py`, `test_m4_ops_foundation.py`, `test_migration.py`, `test_phase3_production_package_v2.py`, `test_phase4_production_gateway.py`, `test_phase4_support_compiler.py`, `test_phase4_v2_native_effects.py`, `test_phase4_v2_support_authority.py`, `test_r3d10_runtime_lts_freeze.py`, `test_r3d2_effective_channel_runtime_context.py`
- `tests/test_openai_cutover.py`, `test_openai_responses_provider.py`, `test_phase1_runtime_bootstrap.py`, `test_v2_drive_archive.py` (added)

The untracked `.pnpm-store/` is preserved tool state, not VCOS source. No raw secret is included in this report.

## 2. Phase 1 — Small Team AI runtime authority

The bootstrap used the authenticated persisted owner/admin path and existing services; it did not insert raw authority rows or alter legacy MR1 projects.

| Authority | Exact value |
| --- | --- |
| Company | `e0b7c806-b39e-4792-bf2e-7e8c6d6ca464` |
| Channel workspace | `a77bc5dc-f7be-4ae0-8523-55fb846d64bd` |
| Workspace identity | `small-team-ai` / `Small Team AI` / `active` / `US` / `en` |
| Active profile | `032178cd-4c7c-4045-85a7-3a84867606d5`, version `4`, input hash `4ec6f32412a450ac3e39f771759ed798926cb7e1a5eb2b7fd0e7a50ab8403db8` |
| Active compiled policy snapshot | `1c8ae53b-da74-45fc-abed-37f466c74843`, version `5`, content hash `3f6c7f8d6186f761020fc6067c709f4216a39da44095672fbdf62f4bbf69da96` |
| Destination | YouTube, `@SmallTeamAI`, manual-only, destination verification remains pending platform identity and is not treated as a publishing authority |
| Duration source | channel duration contract, 6–12 minutes (360000–720000 ms) |

The frozen channel promise is the active profile’s practical, evidence-aware Small Team AI authority: practical AI workflows and automation systems that small teams can understand, trust, and apply safely, without hype. The profile and compiled snapshot hashes above are the durable bindings. Migration 0052 adds the field-level strategic lineage needed for `EditorialIdeaCandidate -> ProjectAdmissionDecision -> VideoProject -> ProductionPackage -> UploadedVideo -> analytics`; it enforces valid intent and audience-hash values once a candidate is selected.

There is deliberately no selected candidate yet, so there is no fabricated candidate-level `audience_promise_hash`, strategic intent, admission, or package. The first selected candidate will use `ACQUISITION` only if the real research/preflight path supports it. This is fail-closed rather than a missing local authority.

| Launch authority | Exact value |
| --- | --- |
| FirstChannelLaunchPolicyVersion | `46ff6f27-357b-4093-bffe-2aab6ec5a473`, version `1`, `APPROVED` |
| Canonical hash | `581d6e450c0e0d54b395966ac15545b3520c4427737406c27c825eea93a970ac` |
| Launch mode | `CONTROLLED_EVIDENCE_BUILDING` |
| Human boundary | `UPLOAD_OR_DO_NOT_UPLOAD`; `MANUAL_ONLY` public publishing |
| Pre-render script/package review | both `false` |
| LaunchRun | `009178fc-2ee5-46f3-8fb2-38403e4693fa`, `ACTIVE`, key `small-team-ai-controlled-evidence-building-v1`, start `2026-08-01` |
| Approved SeriesPlans / active SeriesRuns | `0 / 0` |

No series was invented. The launch policy explicitly has zero initial series plans; a future real, deterministic candidate can take the standalone route with its exact reason code unless an authoritative series is approved. Six legacy v1 projects and five existing slots were discovered and left untouched; bootstrap supersession refuses unsafe mutation when historical production authority exists.

## 3. Phase 2 — Luna/Terra-only OpenAI cutover

### Active provider and lane authority

The provider implementation uses only `POST /responses`, verifies TLS with the certifi CA bundle, does not disable certificate validation, and accepts image inputs only as image MIME/URL content. Raw audio/video is rejected for visual review; the Terra visual lane consumes frames/contact sheet and associated timeline/caption/QC metadata.

| Lane | Model | Reasoning | Fallback / premium / emergency / backup |
| --- | --- | --- | --- |
| `cheap_structured` | `gpt-5.6-luna` | `none` | `[] / null / null / null` |
| `default_multimodal` | `gpt-5.6-terra` | `low` | `[] / null / null / null` |
| `visual_creative_review` | `gpt-5.6-terra` | `medium` | `[] / null / null / null` |
| `long_context_text` | `gpt-5.6-terra` | `medium` | `[] / null / null / null` |
| `engineering_architect` | `gpt-5.6-terra` | `high` | `[] / null / null / null` |
| `gatekeeper_soft_review` | `gpt-5.6-terra` | `medium` | `[] / null / null / null` |

The active router profile is `f8a53558-f2ea-4f4e-8cbd-de6576aac2ca`, provider `OPENAI`, real execution enabled. Both active model profiles declare structured outputs and image input. There is no Sol lane, premium override, automatic premium fallback, or model substitution route.

### Ollama/Sol removal evidence

- `app/providers/ollama.py` was deleted; the active source/config scan for `ollama` returned zero matches (historical migrations/reports excluded).
- The active source/config scan for `gpt-5.6-sol`, Sol fast, premium Sol, automatic premium fallback, and premium override returned zero matches.
- Database scans found no active retired provider/model rows.
- The legacy `video-creator-rag-ollama-1` container was verified as an orphan (its only mount was the named `video-creator-rag_vcos-ollama-data` volume) and removed. Its image and volume were intentionally retained; no Ollama service is running.

### Pricing, budget, readiness, and canary

| Authority | Exact state |
| --- | --- |
| OpenAI pricing snapshot | `ecb158fc-b2f0-41d0-9b4d-09466a02f7b7`, `openai-api-pricing-2026-08-01`, `APPROVED`, hash `8735a7d302d7f9cdeaaa93a73c51f7f1454c9977e9192c96a3a2d174634d0517` |
| Budget policy | `f3e4ba53-aad6-404f-bf04-e49f9ef02cfa`, `ACTIVE` |
| Hard-cap configuration | initial prepaid target USD 15; monthly USD 12; canary USD 2; per lane USD 1; standard tier; no premium or automatic fallback |
| Quota account | `03370fdb-13b9-437c-9015-259d38afe3da`, monthly USD 12.000000; used USD 0.000000; reserved USD 0.000000; `ACTIVE` |
| Credential reference | `openai/openai_api_key`, `REVOKED` |
| Cutover receipt | `0c4c42d3-99cc-476e-b617-95d780678275`, `BLOCKED`, canonical hash `6394378e3e678c6a3f436c4121282125c2aef35d4f41565548b1890d4fda061e` |
| Lane-mapping hash | `465e98334e7b86a77c4b491f40e0ad336d3c54ed76accf28e380eb65edd4cc83` |

Canary inventory and outcome:

| Model | Logical artifacts | Final status | Persisted repair counter | Actual cost |
| --- | ---: | --- | ---: | ---: |
| `gpt-5.6-luna` | 8 | `FAILED` | 24 | USD 0.00 |
| `gpt-5.6-terra` | 14 | `FAILED` | 42 | USD 0.00 |
| Total | 22 | `FAILED` | 66 | USD 0.00 |

The current canary code stops on the first credential rejection rather than making further provider calls. The historical route ledger contains 66 route records: 22 disabled-local routes, 22 pre-repair TLS `PROVIDER_UNREACHABLE` records, and 22 provider-reaching attempts. All 22 provider-reaching calls returned the exact external condition `OPENAI_CREDENTIAL_REJECTED` (HTTP 401). No successful response exists, so token, latency, and actual-cost settlement remain zero. This is an authentication failure, not a schema, prompt, fallback, budget, or quality failure.

`OpenAICutoverService.authorize_rotated_credential(...)` was added and tested: it requires an authenticated actor with `ops.manage` and `provider.execute`, permits only `REVOKED -> CONFIGURED` when `OPENAI_API_KEY` is present, records a domain event without a secret, and then permits a new canary run. The test verifies the rejected case and a successful mocked 22-artifact resumed canary after rotation.

## 4. Phase 3 — MR1 v2 implementation and fail-closed execution state

The new v2 code is implemented and qualified but intentionally has no paid/live production output because `OPENAI_CANARY` is not `PASS`.

- `vcos_v2`, editorial research, production package, readiness, workflow, publishing, operator cockpit, and strategic-lineage models/services now carry exact launch/policy/audience/intent lineage and fail-closed gates.
- `v2_drive_archive.py` implements a no-network, checksum-verified Google Drive archive resolver over an existing `CloudMediaRef`. It makes no Drive upload/API call. The normal gateway retains local native media/render/QC and Drive archive paths; the sealed local archive route is qualification-only.
- No new v2 `ProjectAdmissionDecision`, `VideoProject`, `ProductionPackage`, `ProductionReadinessReceipt`, `FinalMediaRef`, `FinalReviewCandidate`, human upload task, or UploadedVideo was created. The exact final-state counts are all zero.
- Therefore no selected first candidate, media-provider authorization, OpenAI execution authorization, narration, alignment, render, MP4, technical QC, creative QC, Drive verification, or final review candidate exists. This is expected and is not reported as a pass or simulated artifact.
- No ElevenLabs, Pexels, Gemini Image, Google Veo, Google Drive, or YouTube call was made. `YOUTUBE_UPLOAD_CALL_COUNT=0`, `UPLOAD_TASK_CREATED=false`, and no decision was chosen.

The next external media prerequisite is also documented: a Drive root/offload authority, OAuth credential with the required `drive.file` scope for the exact channel, and an authorized uploader that persists a verified `CloudMediaRef` with exact v2 checksums. It was not treated as the current blocker because execution never reached that stage.

## 5. Failure attribution and deployment state

The operator-facing attribution path identifies the first failed stage, deterministic reason code, downstream dependency, provider attempt state, budget state, and safe resume action. The current first failing stage is:

```text
stage=OPENAI_CANARY
reason_code=OPENAI_CREDENTIAL_REJECTED
downstream=MR1 v2 research/admission/package/provider/render/archive/final-review
budget=ACTIVE; USD 12.00 cap; USD 0.00 used; USD 0.00 reserved
```

Docker configuration itself validates and its services now contain only `postgres`, `api`, `frontend`, and `production-workflow-worker`; no Ollama service is declared or running. The current API/worker/frontend images predate this source change: direct image inspection confirmed that the tagged API image lacks `openai_cutover.py`, `openai.py`, `runtime_bootstrap.py`, `v2_drive_archive.py`, and migration 0052. A normal `docker compose up -d --build --remove-orphans`, a plain BuildKit build, and direct base-image pull were attempted but Docker stalled resolving `python:3.13-slim` and `node:22-alpine`; no replacement image was published. The stale containers are not claimed as upgraded runtime code.

This is a separate Docker registry/client availability issue. It does not change the primary Phase 2 blocker: even a successfully rebuilt runtime must not start MR1 until a valid OpenAI credential passes the exact canary.

## 6. Verification and repair evidence

Focused backend qualifications passed after each applicable repair:

- `tests/test_phase1_runtime_bootstrap.py tests/test_ch1_market_profile_v3.py tests/test_openai_responses_provider.py tests/test_openai_cutover.py`: **13 passed**.
- Migration/CLI/readiness/preflight group: **63 passed**.
- V2 package, native effects, support compiler/gateway/authority, Drive archive, final publish, cockpit, effective context, and LTS-freeze group: **107 passed, 1 FastAPI TestClient deprecation warning**.
- M12.2 first-package and full OpenAI rehearsal group: **36 passed, 1 FastAPI TestClient deprecation warning**.
- Strategic-lineage phase 3 group: **15 passed**; phase 4 group: **24 passed**; migration-specific group: **11 passed**.
- Drive/cockpit/native-effects/support/gateway group: **35 passed**.

Final static and frontend verification:

- Scoped Ruff check and `ruff format --check` across 54 relevant changed Python/migration/test files: pass.
- `python -m compileall -q app tests alembic/versions`: pass.
- `git diff --check`: pass.
- TypeScript `tsc --noEmit`: pass.
- ESLint (`--max-warnings=0`): pass.
- Vitest: **13 files, 44 tests passed**.
- Next.js production build: pass; all 18 pages generated.
- `docker compose config --quiet`: pass.
- Alembic heads/current/offline SQL: pass at `0052_vcos_strategic_lineage`.
- OpenAPI generation: **364 paths**, **188 mutation route methods**.
- Active source scans: Shorts active runtime surfaces `0`; Ollama `0`; Sol `0`.

The only non-code warning observed in qualification is the upstream FastAPI TestClient deprecation warning. A broad repository-wide Ruff run still identifies 125 unrelated, pre-existing style findings outside the touched scope; none were masked or reformatted as part of this activation.

## 7. Remaining governed learning work

After a real final video is available and the operator makes the sole `UPLOAD` or `DO_NOT_UPLOAD` decision, the channel may collect YouTube owner analytics. The policy keeps learning conservative: no automatic niche pivot, series kill, or playbook promotion; a channel-level proposal needs at least five comparable videos, and playbook promotion needs at least 30 days unless a stricter policy applies. A single high- or low-view result cannot mutate the channel profile, policy, or series authority.

## 8. Exact external blocker and safe resume

```text
COMPLETED_PHASES=PHASE_1_RUNTIME_BOOTSTRAP, PHASE_2_LOCAL_CUTOVER_AND_OFFLINE_QUALIFICATION, PHASE_3_LOCAL_MR1_V2_IMPLEMENTATION_AND_OFFLINE_QUALIFICATION
BLOCKED_PHASE=PHASE_2_GATE_BEFORE_LIVE_PHASE_3_EXECUTION
BLOCKER_STAGE=OPENAI_CANARY
BLOCKER_CODE=OPENAI_CREDENTIAL_REJECTED
MISSING_EXTERNAL_AUTHORITY=Valid OPENAI_API_KEY with access to gpt-5.6-luna and gpt-5.6-terra
ATTEMPTS_CONSUMED=22 provider-reaching canary requests (66 total recorded routes)
BUDGET_STATE=ACTIVE; monthly cap USD 12.00; used/reserved USD 0.00/0.00
LAST_DURABLE_ARTIFACT=OpenAICutoverReceipt 0c4c42d3-99cc-476e-b617-95d780678275 hash 6394378e3e678c6a3f436c4121282125c2aef35d4f41565548b1890d4fda061e
LOCAL_WORK_COMPLETED=Bootstrap authorities, linear migrations, Luna/Terra-only routing, source/runtime cleanup, v2/Drive implementation, and offline qualification are complete; no simulated MR1 execution was created.
SAFE_RESUME_ACTION=Restore Docker base-image access and run `docker compose up -d --build --remove-orphans`; rotate/inject a valid OPENAI_API_KEY and restart the runtime; then call `OpenAICutoverService(session).authorize_rotated_credential(actor=<authenticated owner/admin with ops.manage and provider.execute>, company_id=UUID("e0b7c806-b39e-4792-bf2e-7e8c6d6ca464"), receipt_id=UUID("0c4c42d3-99cc-476e-b617-95d780678275"))`, followed by the bounded `run_canary(...)`.
```

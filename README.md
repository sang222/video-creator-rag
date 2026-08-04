# VCOS

VCOS is a budgeted, self-funding, multi-channel, artifact-first media workflow engine.

This repository contains the long-form-only VCOS runtime: channel-scoped
profile/policy authority, editorial research and strict admission, immutable
ProductionPackage lineage, durable orchestration, native media rendering/QC,
controlled launch cadence, final-video decision, manual publish verification,
analytics evidence, and the operator dashboard.

## Stack

- Python 3.13+
- FastAPI
- Pydantic v2 and pydantic-settings
- SQLAlchemy 2.x
- Alembic
- PostgreSQL 16
- pytest
- Typer
- PyYAML
- Docker Compose

## Local

```bash
make install
make db-up
make migrate
make seed
make test
make health
```

Dockerized dashboard/API:

```bash
make docker-build
make docker-migrate
make docker-seed
make frontend-up
```

The dashboard runs at `http://localhost:3000` and calls the API at `http://localhost:8000` by default. Keep both URLs on the same hostname so the local httpOnly auth cookie survives refresh. Override `VCOS_FRONTEND_PORT`, `VCOS_API_PORT`, or `NEXT_PUBLIC_VCOS_API_BASE_URL` in `.env` before building the frontend image when needed.

VCOS uses the OpenAI Responses API with `gpt-5.6-luna` for every LLM lane. Configure `OPENAI_API_KEY` only in local environment/secret management; the router persists redacted request identity, usage, pricing version, and actual token-based cost receipts. The lane mapping is source-controlled, OpenAI-only, and has no automatic model fallback.

Provider API keys are env-driven. `.env.example` declares `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `GEMINI_API_KEY`, `PEXELS_API_KEY`, and `PIXABAY_API_KEY`. NativeFFmpegRenderer is the local final assembly authority. Credential references should point to env handles such as `env://OPENAI_API_KEY`, never raw secret values.

Google Veo uses the native Gemini API and exactly one credential, `GEMINI_API_KEY`. Model and pricing truth live in the versioned Veo catalog. `VCOS_VEO_REAL_GENERATION_ENABLED=false` and `VCOS_PA1R_VEO_SMOKE_ENABLED=false` keep execution disabled by default.

YouTube follow configuration is env-driven. `.env.example` declares `YOUTUBE_PUBLIC_MONITOR_ENABLED`, `YOUTUBE_DATA_API_KEY`, `YOUTUBE_OWNER_ANALYTICS_ENABLED`, `YOUTUBE_OAUTH_CLIENT_SECRETS_FILE`, `YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET`, `YOUTUBE_OAUTH_REDIRECT_URI`, and `YOUTUBE_OAUTH_SCOPES`. M10.3 stores API keys and OAuth tokens only through safe references/local ignored dev token files, never as raw DB fields.

Google Drive media offload is env-driven. `.env.example` declares `GOOGLE_DRIVE_OFFLOAD_ENABLED`, `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS_FILE`, `GOOGLE_DRIVE_OAUTH_CLIENT_ID`, `GOOGLE_DRIVE_OAUTH_CLIENT_SECRET`, `GOOGLE_DRIVE_OAUTH_REDIRECT_URI`, `GOOGLE_DRIVE_OAUTH_SCOPES`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `GOOGLE_DRIVE_UPLOAD_MODE`, `VCOS_DELETE_LOCAL_AFTER_DRIVE_UPLOAD`, `VCOS_LOCAL_MEDIA_MAX_AGE_HOURS`, `VCOS_LOCAL_MEDIA_MAX_STORAGE_GB`, and `VCOS_DRIVE_REAL_UPLOAD_SMOKE`. M10.5 uses `drive.file` scope, stores tokens only through `CredentialReference` pointing to ignored local dev token files, and keeps real upload smoke disabled by default.

Dashboard auth is local/dev only in M11.1. `.env.example` declares `VCOS_DASHBOARD_AUTH_ENABLED`, `VCOS_AUTH_MODE`, `VCOS_BOOTSTRAP_ADMIN_EMAIL`, `VCOS_BOOTSTRAP_ADMIN_PASSWORD`, `VCOS_BOOTSTRAP_ADMIN_ROLE`, and `VCOS_AUTH_SESSION_TTL_HOURS` with a 24-hour local login session. Passwords and session tokens are stored hashed; frontend session state uses httpOnly cookies, not localStorage tokens.

M12 readiness is env-driven and guard-first. `.env.example` declares hard budget display values such as `VCOS_BUDGET_MODE=hard_env`, `VCOS_MONTHLY_AI_BUDGET_USD=250`, external-provider caps, and disabled optional spend caps. These are configured monthly caps only; VCOS does not calculate actual spend or remaining budget in M12. Real smoke remains disabled by default and uses provider-specific flags before any external call.

M12 dashboard routes are `/settings`, `/settings/integrations`, and `/providers/readiness`.

## M12 Commands

```bash
vcos integrations readiness
vcos integrations readiness --run-snapshot
vcos integrations smoke --provider openai
vcos integrations smoke --provider youtube-public
vcos integrations smoke --provider youtube-owner
vcos integrations smoke --provider google-drive
vcos integrations smoke --provider google_veo
vcos integrations smoke --provider elevenlabs
```

`vcos integrations smoke` records `SKIPPED` unless the matching real-smoke env guard is enabled. NativeFFmpeg production rendering remains disabled by default. M12 does not add YouTube upload/publish or paid media generation by default.

## M1 Commands

```bash
vcos db migrate
vcos config seed
vcos company create --name "Example Co"
vcos channel create --company-id <company-id> --key saas-ai --name "SaaS AI"
vcos profile create --channel-id <channel-id> --template-key saas_digital_leverage
vcos profile compile --profile-version-id <profile-version-id>
vcos profile activate --snapshot-id <snapshot-id>
vcos profile active --channel-id <channel-id>
```

M1 adds channel profile and immutable policy snapshot backbone only. `NicheProfileTemplate` initializes channel setup; `ChannelProfileVersion` is channel-level profile truth; `CompiledChannelPolicySnapshot` is immutable runtime policy truth.

Future `VideoProject` records must reference an explicit policy snapshot id. Runtime execution must not lookup latest profile or latest snapshot.

## M2 Commands

```bash
vcos project create --company-id <company-id> --channel-id <channel-id> --policy-snapshot-id <snapshot-id> --title "Video" --created-by-user-id <user-id>
vcos artifact create --project-id <project-id> --artifact-type script --created-by-user-id <user-id>
vcos artifact version-create --artifact-id <artifact-id> --created-by-user-id <user-id> --content-json '{"draft":"v1"}'
vcos review create-task --project-id <project-id> --target-type artifact_version --target-id <version-id> --target-artifact-version-id <version-id> --review-type editorial --requested-by-user-id <user-id>
vcos review add-finding --review-task-id <review-task-id> --severity medium --reason-code VALIDATION_FAILED --finding-text "Needs revision" --created-by-user-id <user-id>
vcos revision create --review-task-id <review-task-id> --target-artifact-version-id <version-id> --requested-by-user-id <user-id> --reason "Address finding"
vcos revision resolve --revision-request-id <revision-id> --resolved-by-artifact-version-id <new-version-id>
vcos approval decide --target-type artifact_version --target-id <version-id> --target-artifact-version-id <version-id> --decision approved --decided-by-user-id <approver-user-id>
vcos workflow inspect --project-id <project-id>
```

M2 adds only workflow, review, revision, approval, decision rights, audit/domain event wiring, and future allowance schema fields. `ArtifactVersion` rows are immutable. Approval applies only to exact target versions.

## M3 Commands

```bash
vcos gate seed-definitions
vcos gate run --gate-key rights_copyright_gate --target-type artifact_version --target-id <artifact-version-id>
vcos gate inspect --gate-run-id <gate-run-id>
vcos readiness inspect --project-id <project-id>
vcos policy catalog-create --catalog-key generic_privacy_retention --platform generic --policy-domain privacy
vcos policy version-create --catalog-id <catalog-id> --version 1.0.0 --policy-json '{"rules":[]}'
vcos policy version-activate --policy-version-id <policy-version-id>
vcos policy source-ref-create --policy-version-id <policy-version-id> --source-type OFFICIAL --reliability OFFICIAL --source-url https://example.test/policy
vcos policy change-create --change-key policy-change-1 --platform generic --policy-domain privacy --summary "Manual policy update"
vcos policy revalidate --scope-json '{"targets":[{"target_type":"artifact_version","target_id":"<artifact-version-id>","gate_key":"rights_copyright_gate"}]}'
```

M3 converts M2 allowance JSONB into deterministic gate/evidence contracts. `GateRun` rows are immutable exact-target decision artifacts. Platform policy is a versioned external dependency. M3 performs no LLM/provider calls and does not mutate artifact content or approval decisions.

## M4 Commands

```bash
vcos provider seed-mocks
vcos provider list
vcos provider health-check --provider-key mock_llm
vcos credential ref-create --provider-key mock_llm --credential-key primary --credential-type API_KEY --secret-ref env://MOCK_LLM_API_KEY
vcos credential health-check --credential-reference-id <credential-reference-id>
vcos quota account-create --provider-key mock_llm --quota-limit 100 --unit REQUESTS
vcos quota reserve --quota-account-id <quota-account-id> --amount 1
vcos quota consume --quota-account-id <quota-account-id> --amount 1
vcos cost record --provider-key mock_llm --amount 0 --cost-type ESTIMATED
vcos budget policy-create --policy-key mock_budget --policy-json '{"require_manual_approval_above_usd":5}'
vcos budget check --policy-key mock_budget --estimated-cost 1
vcos provider attempt-mock --provider-key mock_llm --mode success
vcos dead-letter create --queue-name provider_attempts --job-type contract_test
vcos incident create --incident-type HEALTH_DEGRADED --severity WARNING --next-action "Review health."
vcos manual-action create --action-type INVESTIGATE_PROVIDER --target-type provider --next-action "Inspect provider."
vcos system-health snapshot
vcos system-health latest
```

M4 adds provider registry, mock provider interfaces, credential references, quota/cost ledgers, deterministic budget gates, health snapshots, retry/dead-letter records, incidents, manual actions, API, CLI, config catalogs, and tests.

M4 performs no real provider calls and no LLM/content workflow execution.

## Editorial research and admission

Authenticated APIs create editorial research runs, bounded evidence, idea
candidates, and strict niche/market preflights. Research has no production side
effect. The cadence authority may select one deterministic greenlit candidate
and delegate Series/Standalone assignment to ProjectAdmissionService.

## M6 Commands

```bash
vcos production run-create --project-id <project-id>
vcos production execute --production-run-id <production-run-id>
vcos production inspect --production-run-id <production-run-id>
vcos media render-local-smoke --render-spec-snapshot-id <render-spec-snapshot-id>
vcos media qc-run --render-package-id <render-package-id>
vcos media package-inspect --render-package-id <render-package-id>
vcos captions export-srt --caption-track-snapshot-id <caption-track-snapshot-id>
vcos render-spec validate --render-spec-snapshot-id <render-spec-snapshot-id>
```

M6 adds production artifact runs, strict script/narration/caption/visual plan/scene manifest/RenderSpec contracts, source and rights manifests, platform render variants, local fixture rendering, render packages, and MediaQC/AccessibilityQC. M6 uses MockLLMProvider plus local FFmpeg only when available. If FFmpeg/ffprobe is unavailable, local video smoke is BLOCKED with a reason code instead of faking a pass.

## M7 Commands

```bash
vcos publish handoff-create --render-package-id <render-package-id>
vcos publish handoff-inspect --handoff-id <handoff-id>
vcos publish handoff-ready --handoff-id <handoff-id>
vcos publish confirm-manual --handoff-id <handoff-id> --actual-video-id <platform-video-id> --actual-video-url <url> --actual-published-at <iso-datetime> --actual-metadata-json '{"actual_title":"Title","actual_privacy_status":"PUBLIC"}' --actual-disclosures-json '{"ai_disclosure_confirmed":false,"rights_confirmed":true}'
vcos publish confirmation-inspect --confirmation-id <confirmation-id>
vcos publish confirmation-accept --confirmation-id <confirmation-id>
vcos uploaded-video inspect --uploaded-video-id <uploaded-video-id>
vcos uploaded-video list-by-project --project-id <project-id>
vcos uploaded-video summary --uploaded-video-id <uploaded-video-id>
```

M7 turns an M6 render package into an operator-friendly manual publish handoff and captures the human-entered actual publish result. `vcos publish` means handoff/confirmation only. VCOS does not upload, auto-publish, call platform APIs, run OAuth upload flow, schedule uploads, or collect analytics in M7.

## M8 Commands

```bash
vcos analytics sync-create --uploaded-video-id <uploaded-video-id> --sync-mode MOCK
vcos analytics sync-execute --sync-run-id <sync-run-id>
vcos analytics sync-inspect --sync-run-id <sync-run-id>
vcos analytics import-manual --uploaded-video-id <uploaded-video-id> --platform YOUTUBE --platform-video-id <platform-video-id> --captured-at <iso-datetime> --metrics-json '{"views":10}'
vcos analytics snapshot-inspect --snapshot-id <snapshot-id>
vcos analytics list-by-uploaded-video --uploaded-video-id <uploaded-video-id>
vcos analytics metrics-summary --uploaded-video-id <uploaded-video-id>
vcos analytics retention --uploaded-video-id <uploaded-video-id>
vcos analytics traffic-sources --uploaded-video-id <uploaded-video-id>
```

M8 adds local-only analytics sync/import snapshots and an uploaded video metrics read model. `vcos analytics` means snapshot/import/sync only. VCOS does not diagnose no-view, recommend title/thumbnail changes, recover videos, build a dashboard, call real analytics APIs, use OAuth, scrape analytics pages, or fake engagement in M8.

## M9 Commands

```bash
vcos post-publish health-create --uploaded-video-id <uploaded-video-id> --observation-window T_PLUS_24H
vcos post-publish health-execute --run-id <run-id>
vcos post-publish health-inspect --run-id <run-id>
vcos post-publish reports-by-video --uploaded-video-id <uploaded-video-id>
vcos post-publish report-inspect --report-id <report-id>
vcos post-publish proposals-by-video --uploaded-video-id <uploaded-video-id>
vcos post-publish proposal-accept --proposal-id <proposal-id>
vcos post-publish proposal-reject --proposal-id <proposal-id>
```

M9 reads M7 UploadedVideo and M8 analytics snapshots/summaries to create deterministic observation-window diagnostics, failure trace reports, recovery proposals, and manual review actions. `vcos post-publish` is diagnostic and recommendation only. VCOS does not sync analytics, publish, edit platform metadata, auto-reupload, scrape analytics pages, call platform APIs, or use fake engagement in M9.

## M10 API

```bash
POST /learning-candidate-generation-runs
POST /learning-candidate-generation-runs/{run_id}/execute
GET /learning-candidate-generation-runs/{run_id}
GET /learning-candidates
GET /learning-candidates/{candidate_id}
GET /learning-candidates/{candidate_id}/evidence-bundle
GET /learning-review-queue
GET /learning-review-queue/{queue_item_id}
GET /playbook-candidate-drafts/{draft_id}
```

M10 reads M8/M9 evidence and creates learning candidates, evidence bundles,
eligibility gate results, review queue items, and playbook candidate drafts for
human review. M10 does not approve learning, promote playbooks, mutate channel
profile/policy config, call real providers, or change production authority.

## M10.1 API

```bash
GET /llm-router/profiles
GET /llm-router/profiles/{profile_key}
GET /llm-router/lanes
POST /llm-router/smoke-test
GET /human-upload-tasks
GET /human-upload-tasks/{task_id}
```

M10.1 adds guarded OpenAI Luna-only LLMRouter lanes, route attempts, and
ProviderAttempt/LLMRunSnapshot logging. Real execution is disabled by default
and the bounded Responses smoke is skipped unless explicitly enabled.
`UploadedVideo` remains canonical published video truth.

## M10.2 API

```bash
GET /media-provider-roles
GET /media-provider-roles/{provider_key}
GET /media-provider-capabilities
GET /media-provider-capabilities/{provider_key}
POST /media-render-routing/decide
GET /media-render-routing/decisions/{decision_id}
POST /video-projects/{video_project_id}/long-form-render-package
GET /long-form-render-packages/{package_id}
POST /video-projects/{video_project_id}/ai-hero-assets/plan
GET /ai-hero-assets/{asset_id}
POST /video-projects/{video_project_id}/thumbnail-variants/plan
GET /thumbnail-variants/{variant_id}
GET /media-provider-budgets
GET /media-provider-budgets/snapshot
POST /media-provider-gates/capability/check
POST /media-provider-gates/license/check
POST /media-provider-gates/budget/check
POST /media-provider-gates/reused-content/check
POST /media-provider-gates/media-qc/check
```

M10.2 adds provider role/capability metadata, provider/budget/license/reuse/QC
gates, long-form package planning, AI hero planning, thumbnail planning and
license evidence records. Final MP4 assembly belongs exclusively to the
NativeFFmpeg compiled local boundary.

## M10.3 API

```bash
GET /auth/youtube/start
GET /auth/youtube/callback
GET /youtube/connection-status
POST /uploaded-videos/{uploaded_video_id}/youtube/public-sync
GET /uploaded-videos/{uploaded_video_id}/youtube/public-monitor
POST /uploaded-videos/{uploaded_video_id}/youtube/owner-analytics-sync
GET /uploaded-videos/{uploaded_video_id}/youtube/owner-analytics
GET /uploaded-videos/{uploaded_video_id}/youtube/follow-summary
GET /uploaded-videos/youtube/follow-summary
```

```bash
vcos youtube connection-status
vcos youtube public-sync --uploaded-video-id <uploaded-video-id>
vcos youtube owner-sync --uploaded-video-id <uploaded-video-id>
vcos youtube follow-summary --uploaded-video-id <uploaded-video-id>
```

M10.3 follows existing M7 `UploadedVideo` records with YouTube Data API public stats and OAuth-backed YouTube Analytics owner metrics. Public monitor data has WEAK authority. Owner analytics has STRONG authority. Zero values remain zero, missing metrics remain UNKNOWN, unsupported metrics remain NOT_AVAILABLE, and M8 metric truth is updated without inventing metrics. M10.3 does not build dashboard UI, YouTube upload/publish API, YouTube Studio scraping, browser automation, or TikTok/Facebook analytics loops.

## M10.4 API/CLI

```bash
POST /ai-hero-assets/{asset_id}/generate
vcos media ai-hero-generate --asset-id <asset-id>
```

AI hero/metaphor planning uses Google Veo API only for approved 8-second 8-second clips. Workflow, data, diagram, card and UI visuals remain native. NativeFFmpegRenderer performs local final assembly.

## M10.5 API/CLI

```bash
GET /auth/google-drive/start
GET /auth/google-drive/callback
GET /google-drive/connection-status
POST /media/offload-jobs
POST /media/offload-jobs/{job_id}/execute
GET /media/offload-jobs/{job_id}
GET /media/cloud-refs/{cloud_media_ref_id}
GET /video-projects/{video_project_id}/media
GET /render-packages/{render_package_id}/media
GET /uploaded-videos/{uploaded_video_id}/media
POST /media/local-cleanup/run
GET /media/local-retention-policy
```

```bash
vcos drive connection-status
vcos drive offload --path <local-file> --media-type AI_HERO --video-project-id <id>
vcos drive offload-job --job-id <job-id>
vcos drive cloud-ref --id <cloud-media-ref-id>
vcos media cleanup-local
```

M10.5 uploads generated heavy media to Google Drive, verifies Drive file id/web view link/size/checksum where available, stores `CloudMediaRef` in VCOS DB, and cleans local files only after verified upload and retention policy checks. M11 dashboard will use the Drive `web_view_link` as the only human access CTA. VCOS does not expose backend media download or preview proxy routes.

## M11 Dashboard

```bash
GET /dashboard/command-center
GET /dashboard/queues
GET /dashboard/queues/{queue_type}
GET /channels
GET /channels/{channel_id}/workspace
GET /channels/{channel_id}/lifecycle
POST /channels/{channel_id}/lifecycle-decision
GET /uploaded-videos
GET /uploaded-videos/{uploaded_video_id}/dashboard
POST /learning-candidates/{candidate_id}/approve
POST /learning-candidates/{candidate_id}/reject
POST /learning-candidates/{candidate_id}/request-more-evidence
POST /learning-candidates/{candidate_id}/suppress
POST /learning-candidates/{candidate_id}/expire
GET /providers/status
GET /ops/health
```

```bash
cd frontend
npm install
npm run dev
```

M11 adds an action-first operator cockpit for Command Center, channel setup/workspaces, approvals, uploaded video monitoring, Google Drive media CTAs, learning review/playbook promotion, and provider/ops health. Media cards use Google Drive `web_view_link` only and never expose local paths, backend download URLs, or preview proxy URLs.

## Boundaries

## M12.1 Prompt Registry

```bash
POST /prompt-registry/sync
POST /prompt-registry/render
POST /prompt-registry/validate-output
POST /prompt-registry/evaluations/run
```

M12.1 stores canonical prompt assets in `app/prompts/`, syncs versioned prompt/profile/schema records to DB, renders production prompts as `system` + `user` chat messages, binds every content prompt to frozen ChannelProfileVersion/CompiledChannelPolicySnapshot contract refs, stores prompt render/audit snapshots, and validates BaseEnvelope JSON output with safe syntax-only repair.

## Boundaries

M0-M12.1 do not implement auto upload, platform publish APIs, source scraping, unguarded paid media execution, Envato API/download/generation, auto-reupload, fake traffic, bot engagement, or platform evasion systems. Drive/provider smoke remains guarded and off by default. M8 adds analytics snapshots/read models only. M9 adds diagnostics and human-approved proposals only. M10 adds learning review preparation only. M10.1 adds router/derivative/funnel backend contracts only. M10.2 adds provider role/routing/capability/package planning only. M10.3 adds YouTube follow sync/read models only; it still does not upload, publish, or scrape YouTube Studio. M10.5 adds Drive archive/offload only. M11 adds dashboard UI/read models and human review decisions only. M12.1 adds prompt contracts only; it does not call real providers or mutate channel config.

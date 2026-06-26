# VCOS

VCOS is a budgeted, self-funding, multi-channel, artifact-first media workflow engine.

This repository contains M0 foundation, M1 channel profile/policy snapshot backbone, M2 artifact workflow backbone, M3 policy/gate/readiness foundation, M4 provider/cost/quota/ops health foundation, M5 daily run/context/admission foundation, M6 production artifact/local media QC foundation, M7 manual publish handoff foundation, and M8 analytics sync foundation.

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
vcos credential ref-create --provider-key mock_llm --credential-key primary --credential-type API_KEY --secret-ref vault://vcos/mock_llm/primary
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

## M5 Commands

```bash
vcos calendar slot-create --company-id <company-id> --channel-id <channel-id> --policy-snapshot-id <snapshot-id> --slot-date 2026-06-24 --production-goal "Idea envelope"
vcos search evidence-create --company-id <company-id> --channel-id <channel-id> --query "audience query" --source-type MOCK --platform YOUTUBE --search-volume-30d 500
vcos context plan-create --company-id <company-id> --channel-id <channel-id> --policy-snapshot-id <snapshot-id> --slot-id <slot-id> --allowed-sources-json '["channel_profile","policy_snapshot","editorial_slot","search_demand_evidence"]'
vcos context pack-create --retrieval-plan-snapshot-id <plan-id>
vcos channel-state build --company-id <company-id> --channel-id <channel-id> --policy-snapshot-id <snapshot-id> --context-pack-snapshot-id <context-pack-id>
vcos daily run-create --company-id <company-id> --channel-id <channel-id> --policy-snapshot-id <snapshot-id> --slot-id <slot-id> --run-date 2026-06-24
vcos daily execute --daily-run-id <daily-run-id> --mock-mode success
vcos daily inspect --daily-run-id <daily-run-id>
vcos idea preflight --company-id <company-id> --channel-id <channel-id> --daily-run-id <daily-run-id> --daily-idea-decision-id <decision-id> --evidence-json '{"search_demand_evidence_ids":["<evidence-id>"]}'
vcos project admit --daily-run-id <daily-run-id> --daily-idea-decision-id <decision-id> --idea-market-preflight-id <preflight-id> --created-by-user-id <user-id>
```

M5 adds manual daily runs, ResourceResolver MVP, immutable context/state snapshots, safe search-demand evidence, mock LLM proposal capture, deterministic market preflight, and budgeted project admission. M5 uses MockLLMProvider only. LLM output is proposal/draft/rationale only and is captured in `llm_run_snapshots`.

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

## Boundaries

M0-M8 do not implement auto upload, platform publish APIs, dashboard UI, vector/RAG engines, source scraping, OPA/Cedar, real provider integrations, Envato API/download/generation, no-view diagnostics, recovery proposals, title/thumbnail recommendations, or platform evasion systems. M8 adds analytics snapshots/read models only. CapCut pilot notes do not make CapCut a production dependency.

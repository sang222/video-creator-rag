# VCOS

VCOS is a budgeted, self-funding, multi-channel, artifact-first media workflow engine.

This repository contains M0 foundation, M1 channel profile/policy snapshot backbone, M2 artifact workflow backbone, and M3 policy/gate/readiness foundation.

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

## Boundaries

M0, M1, M2, and M3 do not implement media pipelines, agent runtime, publishing, analytics, dashboard UI, provider integrations, queue brokers, RAG, source scraping, OPA/Cedar, or LLM calls. CapCut pilot notes do not make CapCut a production dependency.

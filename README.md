# VCOS

VCOS is a budgeted, self-funding, multi-channel, artifact-first media workflow engine.

This repository contains M0 foundation, M1 channel profile/policy snapshot backbone, and M2 artifact workflow backbone.

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

## Boundaries

M0, M1, and M2 do not implement media pipelines, agent runtime, publishing, analytics, dashboard UI, provider integrations, queue brokers, RAG, policy gates, or LLM calls. CapCut pilot notes do not make CapCut a production dependency.

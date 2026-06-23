# VCOS M0 Foundation

VCOS is a budgeted, self-funding, multi-channel, artifact-first media workflow engine.

This repository currently contains M0 only: repo skeleton, architecture ledger, source-of-truth foundation, initial database schema, config catalogs, contracts, services, CLI, and tests.

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

## Boundaries

M0 and M1 do not implement media pipelines, agent runtime, publishing, analytics, dashboard UI, provider integrations, queue brokers, or LLM calls. CapCut pilot notes do not make CapCut a production dependency.

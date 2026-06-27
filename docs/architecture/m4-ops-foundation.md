# M4 Ops Foundation

## Scope

M4 builds provider, credential, quota, cost, retry, dead-letter, incident, manual-action, and health rails.

Included:

- Provider registry metadata and mock provider catalog.
- Provider interface contracts with deterministic mock providers only.
- Credential references and credential health snapshots.
- Quota accounts and append-only quota events.
- Append-only cost events and deterministic budget gates.
- Provider, component, and system health snapshots.
- Retry policies, provider attempts, dead-letter jobs, ops incidents, and manual action queue.
- API and CLI read/write smoke paths.

## Non-Scope

M4 does not run daily ideas, scripts, RAG, media, publish, analytics, recovery, memory promotion, or dashboard workflows.

No real provider network call is enabled in M4. Tests use mock providers only.

## Provider Registry

`provider_registry_entries` stores provider identity and metadata:

- `provider_key`, name, type, status.
- Capability, policy fit, cost, quota, retry, and metadata JSON blobs.
- No credentials or raw secrets.

Seeded providers are mock-only:

- `mock_llm`
- `mock_tts`
- `mock_media`
- `mock_storage`
- `mock_platform`
- `mock_analytics`

## Credentials

`credential_references` stores references only. `secret_ref` is an env handle such as `env://ELEVENLABS_API_KEY`, not a secret value.

`credential_health_snapshots` preserves credential health history. Missing, expired, revoked, or misconfigured credentials create reason codes and manual actions.

Audit/domain events never include raw secret values or `secret_ref` values.

## Quota And Cost

`quota_accounts` stores current aggregate state. `quota_events` is the append-only audit ledger.

Quota service supports:

- reserve
- consume
- release
- deterministic reject when exhausted

`cost_events` is append-only. M4 records provider costs only. `ESTIMATED` and `RESERVED` are not cash, and M4 does not implement revenue or PnL logic.

Budget gates return deterministic:

- `PASS`
- `REVIEW_REQUIRED`
- `BLOCK`

## Health

M4 records:

- `provider_health_snapshots`
- `component_health_snapshots`
- `system_health_snapshots`

System health aggregates latest component/provider state and active incidents. Degraded or blocked states require `next_action`.

## Retry And Ops

`provider_attempts` records provider contract attempts for mock tests and future use.

Retryable failures that exceed policy create `dead_letter_jobs`. Dead-letter replay changes replay state and emits an event.

`ops_incidents` and `manual_action_queue` are API/CLI-readable operational queues. Every incident/action requires `next_action`.

## M5 Usage Later

M5 may use M4 provider, credential, quota, cost, and health rails before any real LLM workflow is admitted.

M4 itself remains inert for content workflow execution.

## Testing

Use:

```bash
vcos db migrate
vcos config seed
vcos provider seed-mocks
pytest
```

Tests block external provider/network calls and use deterministic mocks.

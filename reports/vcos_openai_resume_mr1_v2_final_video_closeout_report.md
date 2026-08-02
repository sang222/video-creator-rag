# VCOS OpenAI credential resume and MR1 v2 status

**Current state:** `PENDING_SCHEDULED_CADENCE`; no external blocker is active.

The runtime now authenticates with the non-empty `OPENAI_API_KEY` read from the
repository `.env` without revealing its value. The OpenAI-only canary passed,
a new evidence-backed long-form candidate passed strict preflight and is
greenlit, and static provider readiness is ready. The durable cadence worker is
waiting for the policy-owned production-start window; it has not been given a
synthetic clock or a bypass.

## 1. Runtime, database, and configuration state

| Item | Observed state |
| --- | --- |
| Branch / tracked HEAD | `main` / `6c0393f80268368f75273cb50696d0ad3f96b917` |
| Alembic | `0052_vcos_strategic_lineage (head)` |
| Docker | Postgres and API healthy; frontend healthy; production-workflow worker running |
| OpenAI key binding | present inside the API settings boundary; value/fingerprint not emitted |
| VCOS LLM authority | OpenAI Responses API only; `gpt-5.6-luna` and `gpt-5.6-terra`; no fallback |
| ElevenLabs static readiness | `READY_FOR_HUMAN_PAID_APPROVAL` |
| Google Drive archive static readiness | `READY_FOR_FUTURE_EXECUTION` |
| Static readiness network calls | none |

The persisted M12 readiness snapshot is
`8dc021ba-877a-41b9-aedd-e21444897689` with state `PARTIAL`, zero blocking
items, and five expected warnings. Each warning says that an unneeded paid or
analytics smoke was deliberately skipped; it does not represent a failed
provider call or a production block.

The active channel policy already contained the approved non-secret ElevenLabs
voice/model IDs and required Drive archival. `.env` was aligned to that
persisted authority by adding the two IDs and
`GOOGLE_DRIVE_ARCHIVE_ENABLED=true`; Docker was then force-recreated while
explicitly sourcing only the OpenAI key from `.env` to avoid Docker Compose
preferring a stale shell value.

## 2. Immutable historical evidence and OpenAI canary

The original blocked receipt remains immutable:

```text
HISTORICAL_RECEIPT_ID=0c4c42d3-99cc-476e-b617-95d780678275
HISTORICAL_RECEIPT_HASH=6394378e3e678c6a3f436c4121282125c2aef35d4f41565548b1890d4fda061e
HISTORICAL_RECEIPT_STATUS=BLOCKED
HISTORICAL_ARTIFACTS=22
```

Credential rotation creates a distinct deterministic successor receipt rather
than relabelling any failed artifact. It preserves request/route evidence and
the original 401 chain. A local repair also preserved the retry history for a
bad contact-sheet fixture and retried only that non-auth artifact with a valid
PNG.

The completed current receipt is:

```text
OPENAI_CANARY_RECEIPT_ID=b469369f-4fce-4ffb-be80-9f7dac01329b
OPENAI_CANARY_RECEIPT_HASH=35e79cb72fc4e30993abb4800aab4c4ebbf6321db68430ae683695d0697d6a15
OPENAI_CANARY_STATUS=CANARY_PASSED
OPENAI_CANARY_ARTIFACTS=22
OPENAI_CANARY_SUCCESS=22
OPENAI_CANARY_FAILURES=0
OPENAI_CANARY_LUNA=8
OPENAI_CANARY_TERRA=14
OPENAI_CANARY_FALLBACK_LEVEL=PRIMARY_ONLY
OPENAI_CANARY_ACTUAL_COST_USD=0.01054950
OPENAI_QUOTA_USED_USD=0.010550
OPENAI_QUOTA_RESERVED_USD=0.000000
OPENAI_QUOTA_LIMIT_USD=12.000000
```

All 22 persisted route attempts are successful and use only the two approved
models. They recorded 1,548 input tokens and 644 output tokens. No Ollama, Sol,
provider substitution, or model substitution was used.

The user-supplied standalone Responses example was also checked without
exposing the key: its `input=[]` form returns HTTP 400 because Responses
requires input content. The same configuration accepts a non-empty `input`
string. That standalone `gpt-5.4-mini` probe is not part of VCOS production
authority or the governed canary.

## 3. Real MR1 v2 editorial authority

A new manual research run was created instead of reusing the expired migrated
candidate. The persisted demand evidence is for US Google search demand for
`workflow automation`; it records the source's reported monthly volume of
4,400 and competition index of 0.46 as a market signal only, not as an ROI or
time-saving claim.

```text
EDITORIAL_SLOT_ID=79514b83-e8a2-4427-81f9-f2e1f6b74f34
RESEARCH_RUN_ID=f760212f-f08c-441a-b46b-f80be48afda5
SEARCH_EVIDENCE_ID=6e99a502-7ce0-4e5e-9696-4e7d23203ad0
CANDIDATE_ID=4bbacf2b-1781-4ce2-91d7-3914fb25e687
PREFLIGHT_ID=69515b60-b031-4f56-b489-8b84f68c1150
RESEARCH_RUN_STATUS=COMPLETED
PREFLIGHT_DECISION=PASS
PREFLIGHT_DEMAND_SCORE=100
CANDIDATE_STAGE=GREENLIT
```

The selected title is *Workflow Automation for Small Teams: How to Audit One
Process Before You Automate It*. Its scope is deliberately bounded to a
human-in-the-loop workflow audit and explicitly forbids unsupported
time-savings/ROI claims.

## 4. Cadence-owned next action

The approved launch policy owns the next usable long-form slot:

```text
PUBLISH_SLOT=2026-08-04T14:00:00Z
PRODUCTION_START_WINDOW_OPEN=2026-08-02T14:00:00Z
PRODUCTION_START_WINDOW_CLOSE=2026-08-03T14:00:00Z
```

The durable worker scans active launches every 60 seconds and issues a
deduplicated hourly cadence command. Before the start window, it correctly
records `WAIT_OUTSIDE_PRODUCTION_HORIZON`; it must not create a VideoProject
early. When the window opens, the worker will re-evaluate the greenlit
candidate against the live budget/provider authority and may create exactly one
admission and workflow through the ordinary durable path.

No actual ElevenLabs, Drive, Pexels, Gemini, Veo, or YouTube call has been made
by this resume. No VideoProject, render, final media, upload task, upload, or
final-video decision has been fabricated.

## 5. Code repair and verification

The source changes are narrowly confined to preservation/retry behavior for the
OpenAI cutover canary:

- `app/services/openai_cutover.py`
- `tests/test_openai_cutover.py`
- this report

Verification after the final repair/configuration:

```text
tests/test_openai_cutover.py + tests/test_openai_responses_provider.py: 8 passed
python3 -m compileall -q app tests alembic/versions: pass
git diff --check: pass
docker compose exec api alembic current: 0052_vcos_strategic_lineage (head)
```

The test suite ran in a newly created and dropped PostgreSQL test database
inside the disposable API container; it did not touch the production database.
The pre-existing `.gitignore` modification remains preserved and unmodified by
this work. No commit, tag, push, upload, publish, or final decision was made.

## 6. Resume pointer

```text
COMPLETED_STAGES=RUNTIME_KEY_INJECTION,OPENAI_CANARY_PASS,PROVIDER_STATIC_READINESS,EDITORIAL_RESEARCH,STRICT_PREFLIGHT,GREENLIGHT
CURRENT_STAGE=SCHEDULED_LONG_FORM_CADENCE
NEXT_DURABLE_AUTHORITY=LongFormCadenceService worker evaluation at/after 2026-08-02T14:00:00Z
SAFE_RESUME_ACTION=Read the newest CadenceEvaluationReceipt and the resulting admission/workflow; continue only through real durable events and stop at the first genuine external-provider, budget, policy, rights, or human-review boundary.
```

## 7. Durable cadence update — 2026-08-02T13:00Z

The rebuilt worker created the ordinary hourly cadence receipt
`3d93530a-5dd1-413f-8373-a9ddc4dd7ede` at
`2026-08-02T13:00:01.362247Z`. It is immutable and records:

```text
DECISION=WAIT_OUTSIDE_PRODUCTION_HORIZON
PROVIDER_READINESS=READY
ADMITTED_VIDEO_PROJECT_ID=null
PRODUCTION_WORKFLOW_RUN_ID=null
```

This is the expected real state one hour before the authoritative start window.
It supersedes the stale 12:00 provider-configuration block as the latest
cadence evidence, without modifying that historical receipt. No manual action,
provider call, project, workflow, upload, publish, or final decision occurred.

## 8. Durable production outcome — 2026-08-02T14:06Z

The authoritative start window opened and the ordinary cadence worker created
immutable receipt `05a7a122-9c10-4edd-ae14-8e148e31235d` at
`2026-08-02T14:00:52.982183Z`:

```text
DECISION=START_LONG_FORM_PRODUCTION
ADMITTED_VIDEO_PROJECT_ID=9a38dea5-2181-491e-88f8-1bca2505723a
PRODUCTION_WORKFLOW_RUN_ID=1f5103cd-d2f1-4ebb-a9a4-eff4ce441dc9
BUDGET_PROVIDER_READINESS=READY
NO_NETWORK_CALLS_MADE=true
```

Planning, preflight, and admission each reconciled their existing immutable
authority. The first real `RESEARCH` stage command
`fdab80a1-6b51-56e9-be47-2d6903f258d7` then exhausted its bounded five
attempts. Read-only validation of the exact admitted project established the
underlying immutable-authority condition: the v2 project has no
`effective_context_snapshot_id`, so the trusted support compiler raises
`V2_SUPPORT_FROZEN_AUTHORITY_MISMATCH`. No support package, provider plan,
V2 effect-ledger entry, paid provider call, render, archive, upload, or
final-video decision was produced.

The deployed database had retained an older `ops_incidents` check constraint
despite having been stamped through the orchestration migration. That prevented
the worker from recording its terminal incident. A schema-only repair migration
`0053_ops_incident_constraint` now aligns the constraint with the durable
orchestration contract; it modifies no immutable authority. Its application
allowed the real worker to record the final bounded state:

```text
DOMAIN_EVENT_ID=41d7f334-53a6-4792-bdfd-2c430207e0c9
EVENT_STATE=DEAD_LETTERED
LAST_ERROR_CODE=STAGE_RETRY_EXHAUSTED
DEAD_LETTER_JOB_ID=9a5a4fb0-eab1-41cf-8c15-dee9028e89dc
REPLAY_STATE=REPLAYABLE
OPS_INCIDENT_ID=8fdc47e5-89d2-4ea7-b727-d43419a12c5b
OPS_INCIDENT_TYPE=STAGE_RETRY_EXHAUSTED
WORKFLOW_STATE=DEAD_LETTERED
WORKFLOW_STAGE=RESEARCH
V2_EFFECT_LEDGER_ROWS=0
```

This is a genuine human authority boundary. Do not replay the dead letter or
alter the admitted project in place: doing so would only repeat the exact
frozen-authority mismatch. A human must first create/re-admit a project through
the governed path that supplies the required effective-context and frozen
support authorities, then decide whether a new, separately authorized workflow
may start. The OpenAI canary history remains unchanged. The local API and worker
are healthy at Alembic head `0053_ops_incident_constraint`; no commit, push,
upload, publish, or final-video decision was made.

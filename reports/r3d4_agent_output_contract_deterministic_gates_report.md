# R3D4 Agent Output Contract + Deterministic Gates

## Files changed
- `app/services/r3d4.py`: output contract registry, canonicalizer, validation service, deterministic gates, reducers.
- `app/db/models/r3d4.py`, `app/contracts/r3d4.py`: persistence/read contracts.
- `alembic/versions/0027_r3d4_agent_output_gates.py`: validation/gate tables + package status check update.
- `app/services/m12_2.py`: M12.2/M12.2S integration, gatekeeper precedence fix.
- `app/contracts/m12_2.py`: added `WAITING_PROVIDER_CONFIG`.
- Tests: `tests/qualification/test_r3d4_agent_output_contract_gates.py`, M12.2/M12.2S fixtures, migration cleanup/head.

## Output contract registry
- `AgentOutputContractRegistry` defines envelope/artifact requirements for all M12.2/M12.2S agents.
- Package-critical agents require `applied_context_refs`: `effective_context_snapshot_id`, `compiled_policy_snapshot_id`, `channel_contract_hash`, `prompt_context_hash`, `relevant_contract_paths_used`.
- Missing context refs on package-critical output = `BLOCK`; missing artifact fields = `REVIEW_REQUIRED`.

## Canonicalization
- `ArtifactCanonicalizer` converts raw agent output to canonical artifact with `agent_key`, `artifact_type`, `output_type`, schema version, context refs, evidence refs, raw/output/artifact hashes, validation state, reason codes.
- It preserves raw output ref/hash and does not invent missing truth or rewrite business meaning.
- `SchemaViolationLedger` records missing/invalid fields; `StrictRepairService` is schema/envelope-only interface, max 1 attempt.

## Gate schema/status model
- Added persisted `AgentOutputValidationRun`, `SchemaViolationLedger`, `R3D4GateBatchRun`, `R3D4GateRun`.
- Gate statuses: `PASS`, `REVIEW_REQUIRED`, `BLOCK`, `SKIPPED_NOT_APPLICABLE`.
- Gate severities: `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.

## Contract compliance gates
- Implemented umbrella `ChannelRuntimeContractGate` and runtime contract gates for script style, voice profile, visual style, thumbnail, metadata locale, publish timing, rights disclosure, monetization CTA, character runtime, market locale.
- Gates read only `EffectiveChannelRuntimeContextSnapshot`; no latest channel settings, memory, vector, or provider calls.

## Deterministic QC gates
- Implemented `ScriptDurationGate`, `SRTTimingGate`, `VisualCoverageGate`, `ArtifactConsistencyGate`, `DisclosureConsistencyGate`, `UploadCopyTruthfulnessGate`, `ProviderBoundaryGate`.
- Added character/voice readiness/consistency boundary gates.
- Script duration rejects short long-form and fake declared totals; SRT draft/final lifecycle is guarded; visual coverage requires known sentence refs and allowed visual sources.

## Gatekeeper precedence fix
- `_gatekeeper_result()` no longer defaults unknown/empty/ambiguous output to `PASS`; it returns `REVIEW_REQUIRED`.
- `PackageStatusReducer` enforces deterministic `BLOCK` > gatekeeper `PASS`; deterministic `REVIEW_REQUIRED` prevents media-ready.
- Provider-only deterministic blocks resolve to `WAITING_PROVIDER_CONFIG`, not `READY_FOR_MEDIA_PROVIDERS`.

## M12.2/M12.2S prompt path
- After M12.1 schema parse, every raw output goes through R3D4 validation/canonicalization before downstream artifact use.
- Deterministic gate batches are persisted and compacted into `deterministic_gate_report` for audit/prompt context.
- Gatekeeper receives compact deterministic report; full data remains in DB/snapshots/refs.

## Audit/replay
- Prompt render/audit refs remain unchanged.
- New validation/gate rows store raw output hash/ref, canonical artifact hash, checked artifact refs, checked contract paths, effective context snapshot/hash, reducer decision.
- No full-debug data is injected into production prompts.

## Tests run
- `pytest -q tests/qualification/test_r3d4_agent_output_contract_gates.py` → 27 passed.
- `pytest -q tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py` → 12 passed.
- `pytest -q tests/qualification/test_m12_2_first_scripted_video_package.py` → 9 passed.
- `pytest -q tests/test_r3d1_hierarchical_scope.py tests/test_r3d2_effective_channel_runtime_context.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_m12_1_prompt_registry.py tests/test_migration.py` → 38 passed.
- `python -m compileall -q app tests/qualification/test_r3d4_agent_output_contract_gates.py` → passed.
- `alembic heads` → `0027_r3d4_agent_output_gates (head)`.

## Remaining work for M1/R3D5
- M1 upload handoff UI/panel remains out of scope.
- R3D5 should add targeted repair/output contract evolution, section-level repair loops if approved, and richer human review workflows.
- Keep provider/media execution behind explicit boundary + human approval.

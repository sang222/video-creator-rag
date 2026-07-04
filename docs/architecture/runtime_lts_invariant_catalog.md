# Runtime LTS Invariant Catalog

Machine-readable style catalog for `RuntimeLTSFreezeVerifier`.

| invariant_key | severity | expected_status | verification_method | source/module refs |
|---|---:|---|---|---|
| channel_runtime_authority | P0 | PASS | DB query | `VideoProject`, `EffectiveChannelRuntimeContextSnapshot`, `FirstScriptedVideoPackage` |
| channel_profile_policy_immutable_for_agents | P0 | PASS | service import/guard | `ContextPackShapeGate`, Channel Contract refs |
| agent_context_pack_snapshot_required | P0 | PASS | DB query | `AgentContextPackSnapshot` |
| prompt_digest_ref_hash_only | P0 | PASS | DB query + shape contract | `context_pack_hash`, `prompt_context_hash`, `runtime_guard_digest_hash` |
| prompt_budget_and_shape_gates_active | P0 | PASS | service import | `PromptBudgetGate`, `ContextPackShapeGate` |
| prompt_refs_replayable | P1 | PASS | DB query | `PromptRenderRun`, `PromptAuditSnapshot` |
| agent_output_contract_and_canonicalizer | P0 | PASS | service import | `AgentOutputContractRegistry`, `ArtifactCanonicalizer` |
| deterministic_gate_freeze_rules | P0 | PASS | DB query | `R3D4GateRun`, `R3D4GateBatchRun` |
| gatekeeper_unknown_requires_review | P1 | PASS | DB query | `GatekeeperSoftReviewAgent` validation |
| packaging_manual_handoff_read_model | P0 | PASS | service import | `PackagingHandoffReadService`, `ManualPublishOnlyGate` |
| human_upload_backfill_flow_exists | P0 | PASS | service import | `HumanUploadTask`, `PublishHandoffLedgerService` |
| no_youtube_upload_api_route | P0 | PASS | route scan | FastAPI route registry |
| provider_stack_drift_guard | P0 | PASS | DX2 guard | `ProviderStackDriftGuard` |
| provider_stack_docs_frozen | P1 | PASS | doc scan | `docs/architecture/provider_stack_freeze.md` |
| provider_execution_flags_default_false | P0 | PASS | settings schema | `Settings` real execution flags |
| paid_provider_ledger_no_executed_default | P0 | PASS | DB query | `PaidProviderCallLedger` |
| allowed_not_executed_does_not_consume_attempt | P0 | PASS | DB query | `PaidAttemptLimitRecord` |
| memory_prompt_eligibility_rule | P0 | PASS | DB query | `ChannelMemoryItem`, `MemoryFacet` |
| vector_sql_filter_first | P0 | PASS | DB query | `VectorRetrievalManifest` |
| agent_memory_digest_only | P0 | PASS | DB query | `AgentContextPackSnapshot` |
| memory_influence_quality_attribution_exist | P1 | PASS | model import/query | `MemoryInfluenceManifest`, `QualityDeltaAttribution` |
| r3d9_ops_endpoints_get_only | P0 | PASS | route scan | R3D9 ops endpoints |
| r3d9_frontend_no_job_control_buttons | P0 | PASS | source scan | `frontend/src/features/ops/ops-view.tsx` |
| provider_cost_panel_uses_drift_guard | P0 | PASS | source scan | `ProviderCostOpsService` |
| retrieval_manifest_raw_memory_hidden | P0 | PASS | source scan | `RetrievalOpsTraceService` |
| runtime_trace_uses_effective_snapshot | P0 | PASS | source scan | `ChannelRuntimeTraceService` |
| postgres_snapshot_runtime_truth | P0 | PASS | doc/settings check | `source-of-truth.md` |
| no_drive_or_youtube_upload_default | P0 | PASS | settings schema | Drive/YouTube boundaries |
| dx1_semantic_imports_and_wrappers | P1 | PASS | import/doc check | DX1 semantic modules |
| no_schema_history_public_api_break | P0 | PASS | release policy | Alembic/API policy |

Severity:

- P0: freeze blocker.
- P1: freeze blocker or review-required blocker.
- P2/P3: ProductionPainLog unless repeated or elevated by incident owner.

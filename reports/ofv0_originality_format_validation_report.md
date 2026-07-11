# OFV0 - Channel Originality & Format Validation Foundation

Date: 2026-07-11
Scope: deterministic, local/data-only foundation before NR1. No provider, render, upload, or publish execution.

## Verdict

```txt
OFV0_TECHNICAL=PASS
OFV0_HUMAN_REVIEW=PASS
OFV0_FINAL=PASS
PROCEED_TO_NR1=true
```

OFV0 implementation, rehearsal, and human FormatIdentity approval PASS. OFV0 did not run NR1.

## Source and scope

- Read Runtime LTS, provider-stack, post-freeze, production-pain, closeout, PA1-SRT, and NR0-LITE reports.
- The requested NativeFFmpegRenderer research/PDF and latest AI-channel case-study research were not present in the checkout or attachment; no external research/provider call was made.
- `PPL-NR0L-001` bitrate/profile tuning remains deferred and non-blocking.

## Data model and migration

Migration required: existing `artifacts`/`artifact_versions` are scoped to `VideoProject`, while FormatIdentityContract is channel-level and must be versioned/frozen independently. Added `0034_ofv0_originality`:

- `format_identity_contracts`: channel version, frozen profile/context refs, status, content hash, human approval fields.
- `episode_originality_manifests`: one frozen package manifest with contract ref/hash.
- `claim_evidence_ledgers`: per-package declared claim records.
- `synthetic_media_disclosure_receipts`: planned/final disclosure state.
- `platform_native_package_plans`: only `YOUTUBE_LONG` and `YOUTUBE_SHORT`.
- `originality_gate_runs`: immutable, explainable gate evidence.

No Channel Contract, ChannelProfileVersion, or EffectiveChannelRuntimeContextSnapshot is mutated by OFV0 services.

## First-channel FormatIdentityContract draft

Rehearsal contract:

| Field | Value |
| --- | --- |
| Channel | `small-team-ai` |
| Contract ref | `f4ef71b1-6942-49c4-bb69-47244751265d` |
| Version / status | `1` / `APPROVED` |
| Hash | `8522fb38cdfe3ff6ae615d39b7d1c8ff2a6fb34a33363276bd3ebea98a320cbc` |
| Character policy | `NO_CHARACTER` |
| Identity | professional documentary/explainer; operational problem; visualized mechanism; evidence-aware practical takeaway; no recurring synthetic human |

Fixed elements: professional explanatory tone, native diagram/UI/slide backbone, evidence-aware claims, manual-publish packaging.

Must vary: hook family, primary angle, section order, native visual grammar, thumbnail composition, title/metadata pattern, hero concept, stock sequence.

Human-only approval is enforced. Agent approval throws `FORMAT_IDENTITY_AGENT_SELF_APPROVAL_FORBIDDEN`; a new draft creates a new version and already-created package manifests retain their old contract ref/hash. Human approval was recorded by `human-operator` after technical rehearsal.

## Fresh text-only first-channel rehearsal

| Field | Value |
| --- | --- |
| Package | `d9e19d5d-dbfa-4f94-b283-92a5d919e66a` |
| Topic | `How One Automation Can Save a Small Team 20 Hours Every Week` |
| Mode | local text-only compiler; no Ollama/provider media call |
| Package status | `WAITING_PROVIDER_CONFIG` |
| Manifest | `d0bb74e3-eb8c-44ac-a1d8-b165892e176b` / `d0bf32bf52e45c81ec0cab062f0b1c933a6cfdcdf63aabc961928764999d8624` |
| Rolling window | latest 10 approved/published manifests; compared `0` (first OFV0 approved-manifest baseline) |

Episode manifest records topic/angle/insight/value, hook digest, section-order hash, narrative/visual distributions, native diagram/UI moments, AI hero intent, stock IDs, packaging patterns, exact-phrase candidates, and comparison explanation. It stores digests/refs only; no raw previous script is injected into prompts/read model.

## Claim, disclosure, and platform plan

- Claim ledger: `small-team-20-hours-scenario`, type `SCENARIO_BASED`, scope `TITLE`; allowed wording includes `can save`, `illustrative scenario`, and `depends on the baseline workflow`; guarantees are forbidden. Claim gate: PASS.
- Disclosure receipt: `PRE_RENDER_PLANNED`; `NO_CHARACTER`, no realistic AI person, no real-person likeness, limited AI metaphor intent and supporting stock planned. Pre-render disclosure gate: PASS with final-asset confirmation still pending.
- Platform plans: one `YOUTUBE_LONG` canonical plan and one `YOUTUBE_SHORT` standalone-compressed derivative plan. No TikTok/Facebook plan. Both keep manual-publish behavior.

## Deterministic rolling comparison and gates

Comparison is explainable, bounded, and vector-free. Dimensions: exact/normalized title, hook digest/family, section-order hash, narrative/visual distribution, stock sequence, hero concept, thumbnail composition/text, intro/outro, phrase candidates, metadata pattern.

| Gate | Rehearsal result | Explanation |
| --- | --- | --- |
| FormatIdentityCompletenessGate | PASS | Contract v1 is human-approved. |
| EpisodeOriginalityGate | PASS | Unique angle/insight recorded; no approved prior manifest to duplicate. |
| VariationGate | PASS | Substantive must-vary and visual treatment distribution present; transition randomization is not evidence. |
| ClaimEvidenceGate | PASS | `20 hours` scenario has ledger, assumptions, allowed/forbidden wording. |
| DeceptivePackagingGate | PASS | No official-affiliation, fake demo/result, or fake-resource packaging detected. |
| SyntheticMediaDisclosureGate | PASS | Pre-render receipt valid; final confirmation remains required before manual publish. |
| FinalOriginalityGate | PASS | All pre-render OFV0 components pass. `FINAL_ASSET_REVIEW_PENDING` remains a future manual-publish requirement only. |

Hard duplicate script/title-thumbnail/asset-backbone conditions BLOCK. Hook/structure/thumbnail/metadata concentration yields REVIEW_REQUIRED. Same intro/outro is allowed only when the body is materially different.

## Read-model and approval boundary

Added read-only package endpoint:

`GET /video-packages/{package_id}/originality-review`

It exposes plain-language format/originality/claim/packaging/disclosure status, final verdict, compared episode refs, and next action. Technical hashes/reason codes/dimensions are in `technical_details`; no provider/render/upload control is added.

Format contract human actions are isolated at approve/reject endpoints and require operator authentication when enabled. There is no agent auto-approval endpoint.

## Verification

| Command | Result |
| --- | --- |
| `PYTHONPATH=. .venv/bin/alembic heads` | PASS, `0034_ofv0_originality` |
| `PYTHONPATH=. .venv/bin/python -m compileall -q app` | PASS |
| `tests/test_r3d10_runtime_lts_freeze.py` | PASS, 13 tests |
| `tests/test_dx2_provider_stack_reconciliation.py` | PASS, 7 tests |
| `tests/test_r3d9_runtime_dashboard_ops.py` | PASS, 2 tests |
| `tests/test_r3d9_ux2_packaging_review_queue.py` | PASS, 24 tests |
| `tests/test_ofv0_originality_format_validation.py` | PASS, 13 tests |
| UX2 + OFV0 combined rerun | PASS, 37 tests |
| `git diff --check` | PASS |

## Files changed

- `alembic/versions/0034_ofv0_originality_format_validation.py`
- `app/db/models/ofv0.py`, `app/db/models/__init__.py`
- `app/contracts/ofv0.py`
- `app/services/ofv0.py`
- `app/api/routes/originality_review.py`, `app/main.py`
- `tests/conftest.py`, `tests/test_ofv0_originality_format_validation.py`
- OFV0 report/checklist/summary files.

## No-execution proof

The rehearsal only wrote OFV0 text/data artifacts and gate records. Before/after counts were unchanged:

| Entity | Before | After |
| --- | ---: | ---: |
| FinalMediaRef | 0 | 0 |
| CloudMediaRef | 4 | 4 |
| HumanUploadTask | 0 | 0 |
| MediaRenderJob | 0 | 0 |
| ProviderJobSnapshot `SUBMITTED` | 0 | 0 |
| PaidProviderCallLedger `EXECUTED` | 0 | 0 |

No ElevenLabs, Luma, Pexels, Drive, YouTube, provider activation, media render, upload/publish, FinalMediaRef/CloudMediaRef creation, HumanUploadTask creation, Channel Contract/Profile/EffectiveContext mutation, learning promotion, prompt self-mutation, or dashboard execution control occurred.

## P0/P1/P2/P3

No new P0/P1/P2/P3 item was discovered. `reports/production_pain_log.md` was not changed by OFV0.

## Next checkpoint

Human approval and checklist are PASS. `PROCEED_TO_NR1=true` authorizes the next gate; this task does not execute NR1 automatically.

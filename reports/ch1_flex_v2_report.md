# CH1-FLEX v2 Visual Policy Report

## Status

PASS — production activation completed on 2026-07-19 for small-team-ai. MR1 remains ON_HOLD.

## Entry evidence

| Authority | Entry value |
| --- | --- |
| Channel | small-team-ai |
| Immutable profile v1 | f5e45981-51eb-4c24-95a8-f9f5db761195 |
| Immutable v1 snapshot | f9201609-faad-4b68-aebf-b56679d0bde6 |
| Immutable v1 snapshot hash | df3abe8096f8e430520f6a6860fdc27a2a48c12f68fcb3da43c5f8df46a1999a |
| Reused draft v2 | d735ec40-d29f-4d73-9e8a-58b4e1bfe325 |
| VSR1 / IMG1 / VQC1 | PASS / PASS / PASS |
| IMG-CANARY-v3 human review | PASS |
| Drive-verified canary receipt | PASS |

The activation runner required these exact entry IDs and the v1 hash before it could mutate the draft v2 slot. Any drift failed closed.

## Repository mapping

| Requirement | Repository authority |
| --- | --- |
| Typed v2 visual binding | app/contracts/channel_policy.py — ChannelVisualSourcePolicyBinding |
| Gemini Image policy | app/contracts/channel_policy.py — GeminiImageUsagePolicy |
| Exact v2 compiler overlay | app/services/profile_compiler.py — build_ch1_flex_v2_profile_input |
| Qualified evidence binding | app/services/profile_compiler.py — qualified visual-source binding |
| Approval and activation | app/services/channel_profile.py — approve_and_activate_ch1_flex_v2 |
| Guarded activation runner | scripts/activate_ch1_flex_v2_master.py |
| Focused regression | tests/test_ch1_flex_v2_visual_policy.py |

## Exact v2 diff

All unchanged identity and editorial fields were copied from v1. The activation helper accepted only the approved policy paths: policy version/status/approval ref, visual-source binding, Gemini Image provider policy, required capability binding, and budget derivation refs. Missing required paths or any unrelated path blocked activation.

The active v2 binds:

- niche_visual_source_profile = STOCK_ASSISTED;
- exactly one source decision per scene;
- no automatic Pexels-to-AI failover;
- final_composition_authority = NativeFFmpegRenderer;
- native-only exact text and exact number authority;
- generated evidence authority disabled;
- minimum effective output resolution 1080p, with lower resolution blocked;
- final human visual approval and archive verification required;
- the complete closed 13-route visual-source taxonomy.

Pexels remains optional supporting observable-reality footage and is not a global truth source. Authorized assets remain mandatory for actual UI, product, document, and evidence truth. Veo remains a separate high-motion-semantic route.

Gemini Image is bound as google_gemini_image / gemini-3.1-flash-image with 2K, 16:9, one output, one automated attempt, no provider fallback, explicit scoped approval, cost estimate, monthly budget gate, and idempotency. It has no exact-text, exact-number, evidence, fake-UI, generated-logo, or generated-watermark authority. Native overlay is required for exact content.

NICH1 channel fit is compiled at 0.78 by reusing the already approved Pexels semantic-fit threshold. Its authority ref, version, hash, and derivation REUSE_APPROVED_SEMANTIC_FIT_THRESHOLD are emitted in the top-level compiled gate policy. No unrelated 0.75 profile field was invented.

## Production compiler and activation evidence

| Output | Value |
| --- | --- |
| Active profile v2 ID | d735ec40-d29f-4d73-9e8a-58b4e1bfe325 |
| Profile input hash | 7b38a0a3cd1cb8692cf2ffa9e7b4f7dc4ebe33af79b9460f78a7bd7ebeb76bc8 |
| Active compiled snapshot ID | 6304e2a4-f096-410b-af09-a2748b311855 |
| Compiled snapshot version | 3 |
| Compiled snapshot hash | 3b7b2bf83efae2daf93a8d92f6d0afe21ca1a3c96ab1ce2f3744a5bf93574e46 |
| Profile diff audit | d85da191-c154-4f75-81c9-04e38cb6be39 |
| Compiler receipt | 5cdf7314-d5c2-4664-96ac-a2fc9b80e63a |
| Approval audit | 670d093d-408b-4a89-8654-b00682e25f7e |
| Activation audit | 7f510e98-168d-461a-8ee4-0fc68dfd5338 |
| Approval authority | operator-approval://ch1-flex-v2/small-team-ai/master-prompt-2026-07-19 |

The compiler preview was executed twice and produced the same hash. The persisted snapshot matched that preview. New work resolves v2. The rollback pointer still resolves profile v1 and snapshot v1 with the original hash; v1 content was not changed.

## No-provider and no-render proof

Production counts were captured in the same activation transaction.

| Ledger | Before | After | Delta |
| --- | ---: | ---: | ---: |
| ProviderAttempt | 674 | 674 | 0 |
| ProviderJobSnapshot | 0 | 0 | 0 |
| PaidProviderCallLedger | 0 | 0 | 0 |
| LLMRunSnapshot | 675 | 675 | 0 |
| MediaRenderJob | 0 | 0 | 0 |
| FinalMediaRef | 0 | 0 | 0 |
| MediaOffloadJob | 3 | 3 | 0 |
| CloudMediaRef | 4 | 4 | 0 |
| HumanUploadTask | 0 | 0 | 0 |
| UploadedVideo | 0 | 0 | 0 |

Therefore this master task made zero provider, render, Drive, or YouTube calls.

## Verification evidence

- CH1 + NICH1 focused: 22 passed.
- D2P1 focused: 15 passed.
- Required eight-file master suite: 148 passed, 1 non-blocking dependency deprecation warning.
- Shared M5/R3D2/R3D3/R3D4/M12.1/M12.2 suite: 107 passed, 12 historical mock-contract skips, 1 non-blocking dependency warning.
- Alembic: one head, 0037_ch1_flex.
- compileall, Ruff, and git diff --check: PASS.

## Exact next action

Keep MR1 and PKG1 revision on hold. LPRO1 may be started only as a separate operator task; it was not started here.

# CH1-FLEX channel policy report

## Verdict

`small-team-ai` `ChannelProfileVersion v1` is approved and active with compiled snapshot `f9201609-faad-4b68-aebf-b56679d0bde6` (`df3abe…1999a`). Strategy B is channel-scoped data. No provider, production render, Drive upload or YouTube action occurred.

## Existing artifact mapping

| Required concept | Existing/extended artifact |
| --- | --- |
| Initialization preset | `NicheProfileTemplate` / `saas_digital_leverage` |
| Versioned channel truth | existing `ChannelProfileVersion` v1 |
| Format identity | approved `FormatIdentityContract` hash `8522fb…0cbc` |
| Creative quality | existing CQR1 catalog policy `small-team-ai.creative-quality.v1` hash `67b441…99c1` |
| Compiler/snapshot | extended `ChannelProfileCompiler` and `CompiledChannelPolicySnapshot` |
| Project freeze | nullable fields added by migration `0037_ch1_flex`; populated only for new scoped projects |
| Operator workflow | existing channel API/UI extended with draft, edit, validate, preview, diff, submit, approve/reject, activate/rollback |

The original v1 profile input was not rewritten. The initialization catalog is compiled into a new immutable snapshot linked to v1 under the explicit operator approval.

## v1 effective policy

- identity: US, `en-US`, English content, Vietnamese operator, YouTube long-form documentary/explainer;
- visual planning ranges: native `0.50–0.70`, supporting `0.20–0.35`, AI hero `0.05–0.18`;
- Pexels/Veo minimum quotas: `0/0`; no forced alternation or ratio-filling asset selection;
- voice: ElevenLabs `pNInz6obpgDQGcFmaJgB`, `eleven_multilingual_v2`, speed `0.90`, one complete narration, Forced Alignment and canonical timing required;
- final renderer: NativeFFmpeg; Drive verification precedes cleanup; YouTube upload stays manual;
- cost: new/unproven `TIER_1_LOW_COST_PRODUCTION`, one paid attempt/provider/video, one Veo clip/8 seconds/`1.00 USD`, monthly `20.00 USD`, premium experiments disabled;
- originality and publish: approved format identity, same-channel rolling comparison, asset/hero reuse checks, truthful metadata/thumbnail, rights/disclosure and human final approval.

Budget values resolve deterministically from the PA1R `1.00 USD` hard cap, the existing maximum 20 Veo renders/month catalog entry and the CH1-FLEX operator approval. Any overrun or unknown incremental cost requires review.

## Compiler inputs and outputs

Precedence is hard global/company → approved channel contract → approved profile → category → series → project brief → approved override. Literal hard rules cannot be weakened.

The compiler emits the typed channel policy, native render snapshot, provider-use snapshot, CQR1 creative-quality snapshot, publish snapshot, capability evaluation, launch restrictions, input manifest, decision log and content-hashed refs. The capability result is `PASS`; all execution switches in `launch_restrictions` remain false.

## Immutability and fixtures

- Previous active snapshot payload/hash remained unchanged after v1 snapshot activation.
- Both historical VideoProjects retained their prior snapshot IDs.
- Runtime draft v2 `d735ec40-d29f-4d73-9e8a-58b4e1bfe325` changes the native planning band, has hash `9d1a70…449d`, remains `draft`, and is not active.
- The synthetic second-channel fixture calls `compile_channel_policy_blocks` on the same compiler and produces different content without adding a renderer or channel branch.
- Active scoped content cannot compile to a changed output; a new draft is mandatory.

## API/UI surface

The Vietnamese “Hồ sơ & chính sách kênh” tab lists versions and capability status, creates a draft from active, edits typed planning caps, validates, previews, compiles, submits, approves/rejects, activates and rolls back an older approved snapshot. Raw IDs/blockers stay in a collapsed technical section. There is no provider/render/archive/publish button.

## Migration and no-execution proof

Alembic moved from `0036_hpr1_veo` to `0037_ch1_flex`. The migration only adds nullable project freeze columns; it performs no data backfill.

Before/after counts were identical: provider jobs `0`, paid calls `0`, render jobs `0`, FinalMediaRef `0`, HumanUploadTask `0`, UploadedVideo `0`. Existing CloudMediaRef count stayed `4`; CH1-FLEX created none. CQR1/PA1R report hashes remained unchanged.

## Self-repair record

1. Fixture/RBAC: changed the CH1 project creator role to the existing `operator` capability; focused rerun passed.
2. API/read model: semantic diff now resolves catalog-bound v1 effective policy before comparing with v2; focused rerun passed.
3. After the operator explicitly resumed the previously bounded run, removed an unused UI value and stabilized `useMemo`; frontend checks passed.

No gate was weakened and no test was deleted.

## Verification

- required backend suite: `185 passed`;
- frontend typecheck/lint: PASS;
- frontend tests: `35 passed`;
- Next production build: PASS;
- Playwright Chromium E2E: `1 passed`;
- `git diff --check`: PASS.

## Exact next action

Proceed immediately to PKG1 offline package construction. Do not call providers, render, archive, upload or publish.

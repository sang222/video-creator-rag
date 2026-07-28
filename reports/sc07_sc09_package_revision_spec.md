# SC-07 / SC-09 Package Revision Binding Specification

## Status

Đây là specification offline, chưa tạo package revision, chưa approve và chưa
execute:

`SC07_SC09_CONTINUITY_DECISION=PASS_DISTINCT_NATIVE_VISUAL_GRAMMARS`

`SC07_SC09_PACKAGE_REVISION_SPEC=PASS`

`SC07_SC09_AUDIT_FINAL=PASS`

`MR1_FINAL=BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL`

`PROCEED_TO_SC07_SC09_PACKAGE_REVISION=true`

Source package phải được bind chính xác:

- artifact version `d8471bc0-7d58-4b39-a1f9-267d7b8a02b1`;
- content hash `7d827b7b37a654639383f21c6b6e5cd634c64c68f87832b3b9907dbd4b1fa07c`;
- planning output set hash
  `d08a7f10f4073f4d8a10b2190f4ba7cf16b58a073825a6decdcdbaa395563fc3`;
- historical approval `f21fb49d-6695-45f1-be2c-231908f3eb93`, hash
  `5adbf212e6ac6bea6bf3fde4885e0ff3aa7d40829bfb74643bd709b5690b923c`,
  chỉ là lineage, không được reuse.

## Cross-scene continuity

SC-07 và SC-09 cùng native family nhưng dùng visual grammar khác nhau:

| Scene | Route | Grammar | Meaning |
|---|---|---|---|
| SC-07 | `NATIVE_MOTION_GRAPHIC` | horizontal state flow + exception branch + queue | operational control |
| SC-09 | `NATIVE_DIAGRAM` | centered audit card + baseline/pilot/result rail | action plan và decision |

Constraints bắt buộc:

- không dùng stock office/team/paperwork/planning ở cả hai scene;
- SC-07 dùng motion energy cao hơn, branch từ trái sang phải; SC-09 ổn định,
  centered và freeze-frame readable;
- không reuse exception-card layout của SC-07 làm five-field card SC-09;
- chỉ SC-07 dùng queue animation/reason-code badges;
- chỉ SC-09 dùng five-field audit grid và stop/continue conclusion;
- giữ palette, typography, safe regions và US small-business tone của
  VisualDirectionContract; không chèn geography cliché;
- transition SC-07 → SC-08 không để exception queue trở thành unresolved alarm;
  transition SC-08 → SC-09 phải giảm motion energy trước audit card;
- combined diagram density được pacing bằng context reset ở SC-08; không thêm
  diagram vào scene khác;
- không lặp generated metaphor, fake UI, recurring host hoặc stock concept.

Kết quả: route diversity đủ ở grammar/motion/pacing; repetition risk `PASS`.

## Required artifact mutations in a future versioned revision

Chỉ future package-revision workflow được phép tạo các version mới. Approved
package hiện tại không được mutate in place.

### SC-07 SceneVisualIntent

Thay semantic intent bằng:

> Show exceptions leaving the normal path, entering a reason-coded queue,
> preserving original input, reaching a named owner, and ending in pause or
> manual-fallback states.

Bind `S07`, segment hash `c2ba49c0...`, scene timing `292050–345360 ms`.
Scene class `mechanism`; narrative function `primary_explanation`. Persist
requirements hash
`429f9ca4890ff8e9d8375108e01492c64b97c23356cc5186728a6ffcb462b3c4`.

### SC-07 VisualSourceDecision

- preferred route `NATIVE_MOTION_GRAPHIC`;
- fallback class `NATIVE_ONLY`; `fallback=false`;
- stock/provider execution prohibited;
- decision hash
  `0e08a51c6c594ca8cfe8d6eb7a816d639142da3c4978925c06534cb172003d40`;
- bind the four phases and eleven nodes from the audit;
- exact text/number/logo/fake-UI authority `NATIVE_ONLY`;
- no Pexels→AI/Veo/provider substitution.

### SC-09 SceneVisualIntent

Thay semantic intent bằng:

> Present one bounded-handoff audit: trigger, inputs, owner, success condition
> and exception path; then bind baseline, pilot, visible fallback and
> observed-result decision rules.

Bind `S09`, segment hash `0a97f9b1...`, timing `396380–449260 ms`. Scene class
`process`; narrative function `conclusion_action_plan`. Persist requirements hash
`5fa06e281bfea2d5a3a35158620b134fbf264fe1844c5b6b14c026c0d3b6e947`.

### SC-09 VisualSourceDecision

- preferred route `NATIVE_DIAGRAM`;
- fallback class `NATIVE_ONLY`; `fallback=false`;
- decision hash
  `fc3c5cff852ff39e1c9948c63f1e18bf61c5292b4b2ffaefcb148996e9cd1e2d`;
- bind five-field card + baseline/pilot/fallback/result/stop-or-continue rail;
- Pexels revised query authority hash `95554e73...` remains historical rejected
  review evidence and must not appear as executable request;
- no Pexels query family is approved for package binding.

### VisualPlan

Create a new artifact version derived from `7186e7ad-3887-4a8d-9fb4-77c59d9be53d`
(`51248879...`). Replace only `/scenes/6` and `/scenes/8`; keep every other
scene byte-equivalent. Bind native blueprints, timings, overlay-safe regions,
adjacency constraints and both requirements/decision hashes.

### CompiledAssetRequestPlan

Create a new version derived from
`ea2724c5-a8b8-4208-a107-59fe7dabaf2a` (`29436478...`):

- remove executable `pexels:SC-07` and `pexels:SC-09` requests;
- add deterministic native compile requests `native-motion:SC-07` and
  `native-diagram:SC-09`;
- provider request count for these scenes becomes zero;
- preserve requests for unrelated scenes exactly;
- do not reset or copy consumed run ledgers into package planning state.

### ProviderExecutionPlan

Create a new version derived from
`9557bd18-4590-40f2-ab8f-481efdd51d33` (`fcde8935...`):

- remove SC-07 and SC-09 from Pexels operations;
- bind both scenes as provider-free native operations;
- retain `provider_substitution_allowed=false`,
  `automatic_pexels_to_ai_fallback=false`, `fallback=false`;
- no Gemini Image, Veo or other provider operation may be introduced;
- unrelated approved provider operations remain unchanged.

### CostEstimateSnapshot

Create a new version derived from
`d241fd38-935f-4965-94d3-274d1948a163` (`d8e1eb19...`):

- SC-07 native compile estimate: `$0.00` provider cost;
- SC-09 native compile estimate: `$0.00` provider cost;
- Pexels SC-07/SC-09 future operation estimate removed;
- total package estimate recomputed deterministically; do not infer historical
  actual provider cost.

### Rights/provenance and continuity

Create new versions of AssetProvenancePlan, RightsDisclosureCompletenessReport
và SupplementalVisualAlignment:

- origin `VCOS_NATIVE_AUTHORED`;
- source refs are exact script segment, revised SceneVisualIntent,
  VisualSourceDecision and native blueprint hashes;
- no third-party media, likeness, trademark, generated image/video or fake UI;
- persist overlay authority, safe-region contract and continuity constraints;
- old Pexels search/ranking evidence remains historical failure evidence, not an
  asset provenance source.

### PublishRiskDossier

Create a new version from `c39ac7a0-5dea-402b-b5a9-1495ecc84541`
(`79759b18...`). Chỉ update:

- `visual_route_integrity`;
- `provider_plan_binding`;
- `rights_and_provenance`;
- `synthetic_media_disclosure` (`NOT_REQUIRED_FOR_NATIVE_ONLY`);
- `repetitive_visual_risk`;
- `market_and_niche_visual_alignment`;
- `approval_supersession`;
- `mr1_attempt_scope`.

Claims, promise-risk, research, script, metadata, destination và manual publish
boundaries không đổi.

### PackageManifest và approval

PackageManifest mới phải bind toàn bộ new artifact version IDs/hashes, new
planning-output-set hash và exact SC-07/SC-09 route hashes. Không auto-approve.
Human package review/approval mới phải supersede package approval cũ. Sau đó
MR1 approval mới phải bind exact new package hash; approval cũ không được
authorize operation nào.

## Artifacts that remain unchanged

Byte/content authority phải giữ nguyên:

- idea, research, claims, script;
- SpokenTextNormalized và voice policy;
- ChannelProfile v3 `d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711`;
- compiled snapshot v3 authority
  `e6c33d80-f5d8-4f72-9abc-87de3601b89e`;
- TargetMarketProfile `target-market-profile://small-team-ai/v1`;
- NicheAlignmentDossier nonvisual authority `7f9381e8...`;
- MarketAlignmentDossier nonvisual authority `dba5a8cd...`;
- SC-01 đến SC-06 và SC-08;
- destination binding, thumbnail, metadata, voice/narration policy.

Không revise scene khác để làm diff “đẹp”.

## Future attempt and approval scope

| Scene | Provider execution | Route | Model/query | Size | Duration | Max attempts | Cost | Fallback |
|---|---|---|---|---|---:|---:|---:|---|
| SC-07 | no | `NATIVE_MOTION_GRAPHIC` | `N/A_NATIVE` | 1920×1080 | 53.31s | 0 | $0.00 | false |
| SC-09 | no | `NATIVE_DIAGRAM` | `N/A_NATIVE` | 1920×1080 | 52.88s | 0 | $0.00 | false |

Native compile idempotency fingerprint inputs:

`new_approval_content_hash`, `new_package_content_hash`, `run_id`, `scene_id`,
`route`, `requirements_hash`, `decision_hash`, `render_spec_hash`.

Hard invariants:

- old `pexels:SC-07` và `pexels:SC-07:supplement:02` consumed ledgers giữ
  immutable;
- SC-09 unsubmitted ledger không bị consume/reset;
- old package/MR1 approvals là historical lineage only;
- SC-09 không submit dưới approval không bind exact query/route hash;
- SC-07 route mới không execute dưới approval cũ;
- no runtime fallback/provider substitution;
- any execution requires a versioned package revision, explicit human package
  approval và new MR1 approval.

## Zero-execution boundary

Trong audit task: provider calls `4 → 4`; render `0`; Drive `0`; YouTube `0`.
Không Pexels search, download, Gemini, Veo, ElevenLabs, alignment, Drive, FFmpeg
production render hoặc YouTube action.

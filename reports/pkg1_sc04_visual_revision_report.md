# Báo cáo sửa Visual SC-04 của PKG1

## Kết quả

- Technical revision: `PASS`
- Root cause: `INSUFFICIENT_SCENE_SPEC`
- Route mới: `NATIVE_MOTION_GRAPHIC`
- Human package review: `PENDING`
- MR1 execution: `BLOCKED_PENDING_PACKAGE_APPROVAL`
- Provider call trong lúc dựng revision: `0`

## Nghĩa của scene và route

- Authority cũ: scene `SC-04`, role `PEXELS_SUPPORTING`, route `PEXELS_VIDEO`, provider `pexels_api`, attempt cap `1`.
- Semantic intent cũ: Use brief supporting team-work context, then return to a native baseline checklist.
- Semantic intent sửa: Animate the exact S04 narration as a labeled sequence: watch the workflow; request begins; fields are copied; information is missing; resolves the gap; team's own baseline; work that moves information versus work that makes a decision; without hiding responsibility.
- Scene meaning sửa: Watch the workflow before changing it. Record where the request begins, which fields are copied, how often information is missing, and who resolves the gap. A grounded office shot can provide context here, but it cannot prove the process or the time saving. The evidence must come from the team's own baseline. Count completed handoffs. Note rework. Mark the steps that require judgment. Then separate work that moves information from work that makes a decision. Moving clean information is often easier to standardize. Judgment-heavy exceptions should stay visible to a person. The goal is not to remove humans. It is to remove avoidable repetition without hiding responsibility.
- Editorial intent sửa: Use brief supporting team-work context, then return to a native baseline checklist.
- Route mới: `NATIVE_MOTION_GRAPHIC`; mechanism: `BASELINE_CHECKLIST_THEN_INFORMATION_VS_JUDGMENT_SPLIT`.

Native motion plan:

- `OBSERVE_WORKFLOW`: REQUEST_BEGINS, FIELDS_COPIED, MISSING_INFORMATION, GAP_OWNER
- `MEASURE_BASELINE`: COMPLETED_HANDOFFS, REWORK, JUDGMENT_STEPS
- `SPLIT_WORK`: MOVE_INFORMATION, MAKE_DECISION
- `PRESERVE_RESPONSIBILITY`: HUMAN_EXCEPTION_PATH, VISIBLE_OWNER

- Exact text authority: `NATIVE_ONLY`; stock layer allowed: `False`; provider execution required: `False`.

## Package bất biến

- Project: `0578b24a-1898-443e-99bf-add89d3e61e0` (`PKG1_SC04_REVISION`)
- Revision: `88fa9f76-99e8-5ec5-8cdd-63c836031bac` / v3
- Revision hash: `0115137e13399ccb627845347959b285c6622cd5a0df5b4a8f85850e0dde2410`
- Package version/hash: `d8471bc0-7d58-4b39-a1f9-267d7b8a02b1` / `7d827b7b37a654639383f21c6b6e5cd634c64c68f87832b3b9907dbd4b1fa07c`
- Human review task: `d9267746-1baf-48c0-a204-90639e784c57`
- Source package version/hash: `7de25ac8-46e4-46da-b112-f805f16ebaaa` / `200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd`
- Source approval: `ef766b1d-c1a5-43b8-be98-0751bd055653` (`PKG1_MARKET_REVISION_PACKAGE_PLANNING`)
- Source human receipt version/hash: `a35c55b8-6887-4e60-a19c-22928205c572` / `24a2d4c7b0dec7394a8b78ab646f66750fbca35282700d50dcde77bd304c2231`

## Authority Geo/Market

- Ads-only overlay: `72c42303-cdeb-46c4-9b90-881f2f7fd14e` / `d2595b424ab27a5ba84b33aad171251261225e3673531c415c8a55c4a50ea9db`
- Geo closeout evidence: `312474a4-adc1-4979-aec4-a20376e91e0c` / `8650d6ec33fe82848aefc6a4814dbb8e8560e70a297280f94d347d676ee8b178`
- Effective market policy hash: `a0e37064715370a137ddc142a4d844076dc0c0670db4bfb67cd25d9727218b85`
- Không sửa snapshot nền mixed/affiliate; mâu thuẫn được công khai và được overlay bất biến thay thế làm effective truth.

## Hai attempt Pexels được giữ nguyên

| Operation | Artifact version | Hash | State | Failure |
|---|---|---|---|---|
| `pexels:SC-04` | `a057589e-faaf-4ecf-b8ac-fc072a4955dd` | `27678137a4a83b07de60b93d974705b7cd5ffeafa83c8d66390c378f927340f2` | `CONSUMED_FAILED` | `RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE` |
| `pexels:SC-04:supplement:02` | `53fc5e3c-7cab-4ca5-b013-20325dab7a82` | `f9f4714c9b5d7cec9084733f2393fdff0138a5593d96c03d3d9ad533fac000e8` | `CONSUMED_FAILED` | `RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE` |

Query family cũ được tái dựng xác định từ intent đã bind:

- `use brief supporting team-work workplace b roll`
- `use brief supporting team-work close up action`
- `use brief supporting team-work clean composition`

Candidate ranking/semantic score là `UNAVAILABLE_NOT_PERSISTED`; không bịa số liệu.

## Phạm vi provider/cost/rights

- SC-04 attempt cap: `1` → `0`.
- Pexels scene count: `3` → `2`; native scene count: `6` → `7`.
- Google Drive planned mutations: `1` → `2`; exact idempotency phases: CANONICAL_REVIEW_ARCHIVE (PRE_HUMAN_PASS, max=1), FINALIZATION_SUPPLEMENT (POST_HUMAN_PASS_PRE_FINAL_MEDIA_REF, max=1). The supplement does not mutate the canonical review archive.
- Incremental cost: `$0.0`; actual cost: `None`; estimated total/hard cap unchanged: `True` / `True`.
- Rights/provenance: `PASS`, SC-04 source `VCOS_NATIVE`, stock asset required `False`, provider output claimed `False`.

## Gate matrix

| Gate | Verdict |
|---|---|
| `ai_image_eligibility` | `NOT_APPLICABLE` |
| `diagram_suitability` | `PASS` |
| `evidence_truth` | `PASS` |
| `pexels_eligibility` | `PASS` |
| `provider_cost_estimate` | `PASS` |
| `repetitive_production_risk` | `PASS` |
| `rights_disclosure_completeness` | `PASS` |
| `scene_spec_completeness` | `PASS` |
| `semantic_match` | `PASS` |
| `threshold_integrity` | `PASS` |
| `visual_continuity` | `PASS` |
| `visual_market_alignment` | `PASS` |
| `visual_niche_alignment` | `PASS` |

## Diff và ranh giới review

- Changed scenes: `SC-04`; unchanged scenes exact: `True`.
- Không tạo attempt thứ ba, không hạ threshold, không reset ledger, không provider substitution, không runtime fallback.
- MR1 vẫn blocked cho đến khi operator review và PASS đúng package/hash này.

Builder không tạo `ApprovalDecision` và không cấp quyền MR1, render, Drive, YouTube hay provider call.

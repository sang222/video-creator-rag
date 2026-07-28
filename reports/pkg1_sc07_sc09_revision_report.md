# PKG1 SC-07/SC-09 Native Visual Revision — Human Review Packet

Ngày: 2026-07-25
Trạng thái: `TECHNICAL=PASS`, `HUMAN_REVIEW=PENDING`

## Exact review target

```text
review_packet_artifact_version_id=7cf37240-827d-560f-bae1-3be801b2ccf6
review_packet_content_hash=4b19d90cdbc694aeff8ca987d37c89c20816d2e5b3cbc2837c80e478490e0990

revision_id=3b802f4b-44eb-51cd-8254-3cbce868de81
revision_version=4
revision_hash=1a0c369b6445d11b3ab0f18c1a24562cf1a3dcf1f04c298eb77daf914ced7271
revision_content_hash=540bdcb987412de6be06489bca557d1d560bc92a0fc05b2630bf6f02d59cd1c1
bundle_hash=55d415fb8d090093937bf4b26ddc83cbb2071e274054b0ceba78a867aaf6eb85

old_package_artifact_version_id=d8471bc0-7d58-4b39-a1f9-267d7b8a02b1
old_package_content_hash=7d827b7b37a654639383f21c6b6e5cd634c64c68f87832b3b9907dbd4b1fa07c
new_package_artifact_version_id=41c94795-b79e-593a-a065-c663ffca70f1
new_package_content_hash=94cf5cca14f9ca60cf41714f324fa465c0aaa18cd7f28ea4f54756b148694eb0
```

Đây là revision bất biến dạng delta trên exact source package. Source package, approval,
provider evidence và consumed ledgers cũ không bị sửa. Chỉ visual artifacts của SC-07 và
SC-09 thay đổi; idea, research, claims, script, `SpokenTextNormalized`, voice policy,
profile/policy v3, market/niche dossiers, destination, scenes khác, thumbnail và metadata
giữ nguyên theo canonical hash.

## SC-07

Narration yêu cầu người xem hiểu cơ chế xử lý ngoại lệ: ngoại lệ rời normal path, giữ
original input, nhận reason code và named owner, rồi đi tới pause-pilot hoặc manual
fallback. Pexels trả 20 ứng viên, 19 technically valid nhưng best semantic score chỉ
`0.60 < 0.78`; clip stock chỉ cho context, không biểu đạt được quan hệ và state transition.

```text
old_route=PEXELS_VIDEO
new_route=NATIVE_MOTION_GRAPHIC
fallback_class=NATIVE_ONLY
provider_execution_required=false
estimated_cost_class=COST_0_NATIVE
native_plan_artifact_version_id=40d28d4c-23b4-5692-8843-bbae5aa3b72c
native_plan_hash=0018bc4407e88f6cb0f3905794ae85eec07ab5ac76ccf6e1663767727bc58ba4
```

Motion plan:

- Composition ngang: `NORMAL_PATH` → bốn exception classes → `EXCEPTION_QUEUE`.
- Quan hệ bắt buộc: preserve `ORIGINAL_INPUT`; `REASON_CODE` trước `NAMED_OWNER`;
  threshold điều khiển `PAUSE_PILOT`; `MANUAL_FALLBACK` luôn nhìn thấy.
- Relative phases: `0.00–0.20` introduction; `0.20–0.70` mechanism movement;
  `0.70–0.90` outcome emphasis; `0.90–1.00` transition.
- Camera locked orthographic; focal region `x=.12 y=.12 w=.76 h=.62`.
- Labels/numbers là native authoritative overlays; cấm generated text, number, logo,
  fake UI.
- Timing authority là `CanonicalMediaTimeline`; plan không persist production
  milliseconds.

Kết quả: `SC07_NATIVE_MOTION_COMPILATION=PASS`; gate matrix bắt buộc `PASS`.

## SC-09

Narration yêu cầu một audit handoff có năm field (`TRIGGER`, `INPUTS`, `OWNER`,
`SUCCESS_CONDITION`, `EXCEPTION_PATH`), baseline, bounded pilot, visible fallback và
quyết định từ observed result. Revised Pexels query đã bị reject và chưa submit vì
generic office footage không thể truyền đạt đúng labels, order, relationship hay
`STOP_OR_CONTINUE`.

```text
old_route=PEXELS_VIDEO
old_revised_query=REJECTED_NOT_SUBMITTED
new_route=NATIVE_DIAGRAM
fallback_class=NATIVE_ONLY
provider_execution_required=false
estimated_cost_class=COST_0_NATIVE
native_plan_artifact_version_id=7408e680-2bf3-5fdb-811a-7b818d383542
native_plan_hash=822ca71e6bfc698015f9391b0de9089cd49dbe374e936f15d437ef399ad53ecf
```

Diagram plan:

- Type `FIVE_FIELD_AUDIT_WITH_DECISION_RAIL`, centered audit card.
- Edges theo reading order của năm field; `CURRENT_BASELINE → BOUNDED_PILOT →
  VISIBLE_FALLBACK`; `OBSERVED_RESULT → STOP_OR_CONTINUE`.
- Relative phases: `0.00–0.34` audit fields; `0.34–0.55` baseline;
  `0.55–0.78` bounded pilot; `0.78–1.00` observed decision.
- Comparison chỉ là observed result với current baseline; không tạo universal-saving
  claim.
- Exact labels và “twenty-hour” authority là native-only; cấm generated text,
  number, logo, fake UI.
- Timing authority là `CanonicalMediaTimeline`; plan không persist production
  milliseconds.

Kết quả: `SC09_NATIVE_DIAGRAM_COMPILATION=PASS`; gate matrix bắt buộc `PASS`.

## Continuity

`SC07_SC09_VisualContinuityEvidence`:

```text
artifact_version_id=9100c7f2-dbd9-58f4-9649-4a2fd27525fa
content_hash=a9dcddff3f4a3d9485565ce1d5c1de20c81a2992ab531918e31c7a704a258ee9
decision=PASS_DISTINCT_NATIVE_VISUAL_GRAMMARS
```

SC-07 dùng horizontal branching, motion energy cao và settle trước SC-08. SC-09 dùng
centered card, nhịp chậm, freeze-frame-readable và kết thúc bằng decision rail. Cả hai
giữ cùng palette/treatment/caption-safe rules nhưng không lặp stock hoặc generated
metaphor.

## Provider, cost, rights và risk diff

- `pexels:SC-07` và `pexels:SC-09`: `REMOVED_BY_REVISION`.
- Không thêm Gemini Image/Veo; `fallback=false`; provider substitution bị cấm.
- Provider calls removed khỏi future plan: `2`; actual task cost không được fabricate.
- SC-07 và SC-09: `COST_0_NATIVE`.
- Provenance: `source_class=NATIVE_AUTHORED`, `external_provider=false`,
  `generated_evidence_authority=false`, asset state `NOT_CREATED_PLANNING_ONLY`.
- Pexels semantic-fit blocker đã được loại khỏi planning; native plan completeness là
  render precondition mới.
- `UPLOAD_READY=false`, `PUBLISH_EXECUTION_READY=false`,
  `DESTINATION_STATUS=PENDING_PLATFORM_ID`.

## Gate matrix

```text
VisualRealizationCompletenessGate=PASS
PexelsEligibilityGate=PASS_PROHIBITED_NOT_SELECTED
DiagramSuitabilityGate=PASS
EvidenceTruthSourceGate=PASS_NOT_REQUIRED
VisualNicheAlignmentGate=PASS
VisualMarketAlignmentGate=PASS
SemanticMatchGate=PASS
VisualContinuityGate=PASS
RepetitiveProductionRiskGate=PASS
RightsDisclosureCompletenessGate=PASS
ProviderCostEstimateGate=PASS
PackageIntegrityGate=PASS
```

Offline compilation dùng projection từ exact canonical timeline hash
`eada2c27cadb13eef03d6f160c064807dc63369b77b66192e127a9cbef2bd994`; projection chỉ
dùng để validate và không thay production timing authority.

## Supersession và approval scope

Historical package approval `77f2fe34-2099-48ad-88e0-2d74a25bfa9e` và MR1 approval
`f21fb49d-6695-45f1-be2c-231908f3eb93` được đánh dấu
`SUPERSEDED_BY_SC07_SC09_REVISION` cho future execution, không bị xóa. Approval
`40193854-8633-45a5-97be-54b380a8c8e5` tuyệt đối không được reuse. Hai SC-07 Pexels
ledgers vẫn `CONSUMED_FAILED`; SC-09 vẫn `NOT_SUBMITTED`.

Operator chỉ review exact revision/package/native-plan hashes nêu trên. Scope không
cho phép provider call, render, Drive, YouTube hoặc publish execution.

## Operator action

Trả đúng một trong hai:

```text
PASS
```

hoặc:

```text
REJECT: <reason>
```

`PASS` mới cho phép persist human receipt và tạo fresh MR1 re-approval; không tự động
thực thi MR1.

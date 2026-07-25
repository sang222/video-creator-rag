# MR1-REAPPROVAL v3 — exact real-production authority

Ngày: 2026-07-19  
Kênh: `small-team-ai`  
Kết quả: **PASS**  
Execution: **NOT_STARTED**

## Exact approval

ApprovalDecision mới `4ccc7185-e760-4470-aba9-857ab0a18f77` cấp authority
`MR1_REAL_PRODUCTION_EXECUTION` duy nhất cho project
`2522a8f1-1ea4-4d66-8ea5-411aaa8f152b`, revision
`a90e2786-f6e0-5480-94a4-fb28fd000edf` v2 và package ArtifactVersion
`7de25ac8-46e4-46da-b112-f805f16ebaaa`.

```text
approval_ref=mr1-approval://small-team-ai/2522a8f1-1ea4-4d66-8ea5-411aaa8f152b/a90e2786-f6e0-5480-94a4-fb28fd000edf/v1
approval_content_hash=4a8c259debc1ae3f94feb7c5be959e0d42bca048911b052a221eda7373d1c25c
decision=APPROVED
decision_source=OPERATOR
execution_mode=REAL_APPROVED_PRODUCTION
single_run=true
terminal_after_execution_begins=true
publishable=false
```

Exact package hash là
`200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd`;
revision hash là
`b50ff5d3bcbf07de4b709ae0d9017a9df04fec49481fb14c224a709c85b0875b`;
reviewed snapshot hash là
`f5e4932ddac49a20216dd0b16415d0bb378bfd2e970a1dce3c3736ad1c0510b2`.
Authority nguồn là PKG1 planning approval
`ef766b1d-c1a5-43b8-be98-0751bd055653` và immutable human receipt
`a35c55b8-6887-4e60-a19c-22928205c572` /
`24a2d4c7b0dec7394a8b78ab646f66750fbca35282700d50dcde77bd304c2231`.

## Immutable artifacts

| Artifact | ArtifactVersion | Content hash |
|---|---|---|
| MR1 approval receipt | `d875858d-46fe-4ce5-a89c-785f266c6b4c` | `749f167ecce6309a42f67330ea829a530468eea5ee332fa662567b0017c4e11d` |
| Read-only readiness preflight | `432f42be-3a17-400a-a97d-2658b05a2ebc` | `57147774df5f982dacc435e0adb622a5471a9f0f9b4cff3dea6ceef402c56bd8` |
| Append-only supersession ledger | `ef9b30bc-3dde-4691-bc0c-61b817cdcdfc` | `6da137e86171beabbce482a97e2dbb13343add8e776b10ddaef0236218d9181d` |

Stored và recomputed hash của cả ba artifact đều khớp. Receipt chứa profile,
snapshot, market, destination, provider plan, cost, rights/disclosure, LPRO1,
human/Drive/FinalMediaRef policy và execution terminal policy.

Old MR1 approval `ba688de8-4274-4414-9be1-e8dda827b97e` không bị sửa hoặc xóa.
Ledger mới ghi `SUPERSEDED`, `PRESERVED`, `reuse_allowed=false`; historical
evidence vẫn immutable.

## Binding, attempts và cost

Profile v3 `d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711` có hash
`1c96bd4dd254ae04f57ba3d7156eb4bc612aeeedc59d2c8e65dda369cd627640`.
Compiled snapshot gắn profile v3 là ID
`e6c33d80-f5d8-4f72-9abc-87de3601b89e`; persisted snapshot version thực tế là
4, hash
`12b66551bd9bdfce1d59d1019ff50bc1c49756b6dc4ab505fde080630b4551bc`.
Không relabel version này thành 3.

NicheAlignmentDossier và MarketAlignmentDossier đều exact PASS. Target authority
là US / `en-US` / `America/New_York` / USD / `STOCK_ASSISTED`. Destination vẫn
YouTube `@SmallTeamAI`, `platform_channel_id=null`, `PENDING_PLATFORM_ID`, manual
publish.

Provider scope đọc nguyên vẹn từ ProviderExecutionPlan:

- ElevenLabs narration: 1 planned, cap 1.
- Forced Alignment: 1 planned, cap 1.
- Pexels: `SC-04`, `SC-07`, `SC-09`; mỗi scene cap 1.
- Gemini Image: 0 effective call.
- Google Veo: 0 effective call.
- Native: `SC-01/02/03/05/06/08`.

Automatic retry, provider switch, Pexels-to-AI fallback và external AI-video
fallback đều bị cấm. Cost snapshot khóa USD, estimate `0.00`, hard ceiling
`1.00`, approval amount `1.00`, `actual_cost=null`.

LPRO1 execution contract mới có hash
`f62b0bf918c63f67ed2824cfa45de36ab31b99e6cc9f138a6c7844be6b8aacdf`,
bind orchestrator `lpro1.long-production-orchestrator/1.0.0`, render contract
`lpro1.long-form-render-package.v1` và production envelope v1.

## Readiness, human gate và publish boundary

Read-only preflight xác nhận ElevenLabs, Pexels, Gemini và Drive credential đều
được cấu hình; Drive root và FFmpeg/FFprobe sẵn sàng. Không probe provider và
không bật kill switch trong Prompt 1.

```text
MR1_RENDER_DESTINATION_GATE=PASS
PUBLISH_DESTINATION_GATE=BLOCKED_PENDING_PLATFORM_ID
UPLOAD_READY=false
PUBLISH_EXECUTION_READY=false
```

Approval yêu cầu TechnicalMediaQC PASS, CreativePerceptualMediaQC được operator
chấp nhận, exact final MP4 hash được full-watch, Drive archive VERIFIED và
rights/provenance complete. Trước human PASS chỉ được tạo ReviewMediaCandidate;
`FinalMediaRef` chưa được tạo.

## Zero-execution proof và verification

Production transaction chỉ thêm 1 approval và 3 governance artifacts. Hậu kiểm:
paid provider call=0, provider job=0, render=0, FinalMediaRef=0,
HumanUploadTask=0, UploadedVideo=0. Deltas provider/render/Drive/YouTube đều 0.

- Alembic: `0038_lpro1_daily_mode (head)`.
- Compileall: PASS.
- Focused MR1 suite sau một repair cycle: 3 passed.
- Required cross-phase acceptance suite: 29 passed, 1 existing warning.
- `git diff --check`: PASS.

## Verdict

```text
MR1_REAPPROVAL_ENTRY=PASS
MR1_REAPPROVAL_EXACT_TARGET=PASS
MR1_REAPPROVAL_HASH_REVALIDATION=PASS
MR1_REAPPROVAL_PROFILE_V3_BINDING=PASS
MR1_REAPPROVAL_TARGET_MARKET_BINDING=PASS
MR1_REAPPROVAL_MARKET_ALIGNMENT=PASS
MR1_REAPPROVAL_DESTINATION_BINDING=PASS
MR1_REAPPROVAL_PROVIDER_PLAN=PASS
MR1_REAPPROVAL_COST_SCOPE=PASS
MR1_REAPPROVAL_RIGHTS_DISCLOSURE=PASS
MR1_REAPPROVAL_LPRO1_CONTRACT=PASS
MR1_REAPPROVAL_APPROVAL_RECEIPT=PASS
MR1_REAPPROVAL_READINESS=PASS
MR1_REAPPROVAL_PUBLISH_BOUNDARY=PASS
MR1_REAPPROVAL_REPAIR_CYCLES=1
MR1_REAPPROVAL_PROVIDER_CALLS=0
MR1_REAPPROVAL_RENDER_CALLS=0
MR1_REAPPROVAL_DRIVE_CALLS=0
MR1_REAPPROVAL_YOUTUBE_CALLS=0
MR1_REAPPROVAL_FINAL=PASS
MR1_EXECUTION=NOT_STARTED
MR1_PROVIDER_CALL_COUNT=0
MR1_RENDER_STATUS=NOT_STARTED
MR1_HUMAN_REVIEW=PENDING
PUBLISH_DESTINATION_STATUS=PENDING_PLATFORM_ID
PUBLISH_EXECUTION_READY=false
PROCEED_TO_MR1=true
```

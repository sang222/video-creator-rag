# PKG1-MARKET-REVISION — báo cáo kỹ thuật

Ngày: 2026-07-19  
Kênh: `small-team-ai`  
Kết quả kỹ thuật: **PASS**  
Human review closeout: **PASS**

## Kết quả production

PKG1 đã được dựng lại dưới authority Market v3 bằng một revision mới; PKG1 v1 và toàn bộ approval/evidence lịch sử vẫn immutable. Operator đã trả literal `PASS`; Prompt 2 tạo đúng một package-planning approval, immutable receipt, MR1 re-approval readiness và operator read model.

| Đối tượng | Exact authority |
|---|---|
| VideoProject revision | `2522a8f1-1ea4-4d66-8ea5-411aaa8f152b` |
| Revision | `a90e2786-f6e0-5480-94a4-fb28fd000edf` · v2 |
| Revision hash | `b50ff5d3bcbf07de4b709ae0d9017a9df04fec49481fb14c224a709c85b0875b` |
| Package ArtifactVersion | `7de25ac8-46e4-46da-b112-f805f16ebaaa` |
| Package hash | `200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd` |
| Planning output set hash | `b9f3dda910bf3b08d0e6b3173f8be32247ca4f7dc63f5224749eb75b99d906f0` |
| Exact review task | `a99f2ad4-9b1d-4bc6-bafc-024ecd7e9c56` · `completed` |
| ApprovalDecision | `ef766b1d-c1a5-43b8-be98-0751bd055653` · `PKG1_MARKET_REVISION_PACKAGE_PLANNING` |

## Entry và lineage

- Entry evidence giữ nguyên: `LPRO1_FINAL=PASS`, `GEO1_FINAL=PASS`, `GEO2_FINAL=PASS`, `CH1_MARKET_V3_FINAL=PASS`, `PROCEED_TO_PKG1_REVISION=true`.
- Revision supersede project PKG1 lịch sử `e601fde5-e502-4c38-ae51-dd7e8d149b4e`; không sửa IdeaDecision, research/source/claim evidence, script, historical plan, cost snapshot hoặc approval receipt.
- Script được reuse đúng ArtifactVersion `4c0ac729-32c5-4005-9078-013b399e8802`, hash `48e89e95a3234f7f3abd1f86a99dd2e8009279e22dea511ede299483baa14fd7`.
- Category authority là `AI workflows` / `operator_series`; production goal và editorial slot được hash-bind. LPRO1 contract là `lpro1.long-form-render-package.v1`.
- Cùng frozen inputs tạo cùng revision UUID/hash; closeout idempotency post-check giữ nguyên 1 project, 36 artifacts, 37 versions, 1 completed review task và đúng 1 planning approval.
- Mọi thay đổi sau quyết định của operator phải tạo version mới.

## Exact market bindings

| Binding | Ref / hash |
|---|---|
| ChannelProfileVersion v3 | `d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711` / `1c96bd4dd254ae04f57ba3d7156eb4bc612aeeedc59d2c8e65dda369cd627640` |
| Compiled snapshot v3 | `e6c33d80-f5d8-4f72-9abc-87de3601b89e` / `12b66551bd9bdfce1d59d1019ff50bc1c49756b6dc4ab505fde080630b4551bc` |
| Channel Contract | `compiled-policy-snapshot://e6c33d80-f5d8-4f72-9abc-87de3601b89e/channel-contract` / `47ef8716145fb781471293d864f82cc8721a6e79f466a31e1ce0351c20b2b988` |
| TargetMarketProfile v1 | `target-market-profile://small-team-ai/v1` / `d456033a947408f671b328f9c5f5589ae86ea4529caf60b18c3d913058d1bb9e` |
| TargetMarketDigest | `target-market-digest://small-team-ai/profile-v1` / `244989186381a71c4eda812743b3b095426397ae0cdfb791641b2875918014f0` |
| DestinationBinding v1 | `destination-binding://small-team-ai/v1` / `411aae66418315da8e6a0bf2cd23e896e89e7cd4827a5b54c36c0437ad63efab` |

Market authority là US / en-US / America/New_York / USD, narration `en-US`, localization `EN_US_MASTER_ONLY`, visual profile `STOCK_ASSISTED`. Destination vẫn đúng sự thật: YouTube `@SmallTeamAI`, `platform_channel_id=null`, credential ref `null`, `PENDING_PLATFORM_ID`.

## Gate và artifact trọng yếu

| Artifact | ArtifactVersion / hash | Trạng thái |
|---|---|---|
| NicheAlignmentDossier | `7f9381e8-0697-4b77-9f55-d25d72afd547` / `d6d82fc26373f46e13c75d61068b18c3f23176bb21046ef193240e84bb29703d` | `PASS` |
| MarketAlignmentDossier | `dba5a8cd-ca61-49e8-b662-c27ad7f02959` / `57b87274528a909db91417071aae687baa60d33ee9aac90731819cf5bbd4c969` | `PASS` |
| TargetMarketConsistencyCheck | `32831f52-c7f0-4379-a25c-aab08a112ddd` / `3314fae1c640828c654deabe6adc5da3a465af60708a879c93bfeea3e1ee37ea` | `PASS` |
| PublishRiskDossier | `c5af5a04-2c72-4c12-a1cc-d930454da1df` / `aa20b504ad9b1faeb9512c3ff7e5d693a82e464e071be8ab1fff0e868b91dcdb` | content `REVIEW_REQUIRED`; execution `BLOCK` |
| PublishHandoffPackage | `774466a6-0978-429a-873c-a019dbd55268` / `6b88bd76de668cbd7df4e9d603d130fa6d68eda4d02fae783220048b876c4313` | `DRAFT`; pending media |

Tất cả market gates bắt buộc có exact subject evidence và `PASS`: idea/topic, research jurisdiction, script, voice locale, visual, thumbnail và metadata. Niche dossier không được dùng thay market dossier.

- Voice policy khóa `content_language=en`, `narration_locale=en-US`, pronunciation và approved voice identity; không gọi TTS.
- Visual plan giữ policy v3: Native cho mechanism/labels/numbers, Pexels chỉ observable supporting footage, Gemini Image chỉ authored still với native overlay, Veo chỉ khi motion có semantic value. Không có auto Pexels-to-AI fallback.
- Thumbnail v2 và metadata đều `en-US`, bind exact script/claim/market/dossier; title giữ illustrative scenario, không biến “20 hours” thành measured result.
- ProviderExecutionPlan chỉ là kế hoạch, có attempt caps/approval/idempotency requirements. CostEstimateSnapshot bind current versioned budget, Gemini Image và Veo catalogs; actual billed cost không được suy đoán.
- Rights, provenance và synthetic-media disclosure là planning evidence; generated evidence authority luôn `false`.

Danh sách đầy đủ artifact ID, ArtifactVersion và hash nằm tại `reports/pkg1_market_revision_inventory.json`.

## Publish boundary và no-execution proof

Publish risk tách riêng:

- nội dung/market/consistency: technical `PASS`, chờ operator review;
- publish window: `REVIEW_REQUIRED` vì mới là hypothesis;
- publish execution: `BLOCK` do `PENDING_PLATFORM_ID`, chưa có final media, media QC và human media review;
- package: `UPLOAD_READY=false`, `PUBLISH_EXECUTION_READY=false`, `MARKET_PACKAGE_FROZEN=false`.

Không tạo `FinalMediaRef`, `HumanUploadTask`, `UploadedVideo` hoặc fake file ref. Delta thực thi của revision: provider=0, render=0, Drive=0, YouTube=0. Prompt 2 đã closeout package planning; `MR1_REAPPROVAL_ENTRY=READY`, nhưng MR1 execution chưa được approve hoặc chạy.

## Human approval closeout

- ApprovalDecision: `ef766b1d-c1a5-43b8-be98-0751bd055653`, exact target `7de25ac8-46e4-46da-b112-f805f16ebaaa`.
- Reviewed snapshot hash: `f5e4932ddac49a20216dd0b16415d0bb378bfd2e970a1dce3c3736ad1c0510b2`.
- Immutable human receipt: ArtifactVersion `a35c55b8-6887-4e60-a19c-22928205c572`, hash `24a2d4c7b0dec7394a8b78ab646f66750fbca35282700d50dcde77bd304c2231`.
- MR1 readiness: ArtifactVersion `185f1b3d-bbba-44d3-845f-543ad940c91e`, hash `8f5fe8000287222c4e6bc72d0645d4d55efc510a528f8f8a3d9e655fccecfeea`.
- Operator read model: ArtifactVersion `db5740e2-8350-44b5-84c4-41e1d4b00803`, hash `22b1b1b19c51cf6bf42088d51ecd389e4cf2c5499596b92baa0ee64b7df0a0d1`.

Raw reviewed package bytes/hash không đổi và vẫn lưu pre-approval fields `PENDING` / `WAITING_HUMAN_REVIEW`; effective projection là `PASS` từ approval + immutable receipt + completed review. Old MR1 approval `ba688de8-4274-4414-9be1-e8dda827b97e` không bị rewrite hoặc xóa và được đánh dấu lineage `SUPERSEDED_BY_PKG1_MARKET_REVISION`, `reuse_allowed=false` trong receipt/readiness mới.

## Verification

- Alembic: một head `0038_lpro1_daily_mode`.
- Python compile: PASS.
- Prompt 1 exact acceptance/regression suite: 45 tests PASS; một warning deprecation hiện hữu.
- Prompt 2 required suite: 26 tests PASS; một warning deprecation hiện hữu.
- Focused closeout/revision suite sau repair: 8 tests PASS; một warning deprecation hiện hữu.
- Dashboard/read-model regression: 6 tests PASS; một warning deprecation hiện hữu.
- Production idempotency/safety post-check: PASS.
- `git diff --check`: PASS.

## Verdict

```text
PKG1_MARKET_REVISION_ENTRY=PASS
PKG1_MARKET_REVISION_LINEAGE=PASS
PKG1_MARKET_REVISION_PROFILE_V3_BINDING=PASS
PKG1_MARKET_REVISION_TARGET_MARKET_BINDING=PASS
PKG1_MARKET_REVISION_MARKET_ALIGNMENT=PASS
PKG1_MARKET_REVISION_DESTINATION_BINDING=PASS
PKG1_MARKET_REVISION_VOICE_POLICY=PASS
PKG1_MARKET_REVISION_VISUAL_PLAN=PASS
PKG1_MARKET_REVISION_THUMBNAIL=PASS
PKG1_MARKET_REVISION_METADATA=PASS
PKG1_MARKET_REVISION_PROVIDER_PLAN=PASS
PKG1_MARKET_REVISION_COST_ESTIMATE=PASS
PKG1_MARKET_REVISION_RIGHTS_DISCLOSURE=PASS
PKG1_MARKET_REVISION_PUBLISH_RISK_DOSSIER=PASS
PKG1_MARKET_REVISION_PUBLISH_PACKAGE_PLAN=PASS
PKG1_MARKET_REVISION_UPLOAD_READY=false
PKG1_MARKET_REVISION_PUBLISH_EXECUTION_READY=false
PKG1_MARKET_REVISION_DESTINATION_STATUS=PENDING_PLATFORM_ID
PKG1_MARKET_REVISION_REPAIR_CYCLES=5
PKG1_MARKET_REVISION_PROVIDER_CALLS=0
PKG1_MARKET_REVISION_RENDER_CALLS=0
PKG1_MARKET_REVISION_DRIVE_CALLS=0
PKG1_MARKET_REVISION_YOUTUBE_CALLS=0
PKG1_MARKET_REVISION_CLOSEOUT_ENTRY=PASS
PKG1_MARKET_REVISION_EXACT_TARGET_RESOLUTION=PASS
PKG1_MARKET_REVISION_HASH_REVALIDATION=PASS
PKG1_MARKET_REVISION_HUMAN_REVIEW=PASS
PKG1_MARKET_REVISION_APPROVAL_RECEIPT=PASS
PKG1_MARKET_REVISION_SUPERSESSION_LINEAGE=PASS
PKG1_MARKET_REVISION_READ_MODEL=PASS
PKG1_MARKET_REVISION_FINAL=PASS
PRODUCTION_PACKAGE_APPROVED=true
MR1_REAPPROVAL_ENTRY=READY
MR1_EXECUTION=NOT_STARTED
MR1_PROVIDER_CALL_COUNT=0
MR1_RENDER_STATUS=NOT_STARTED
MR1_HUMAN_REVIEW=PENDING
PKG1_MARKET_REVISION_CLOSEOUT_REPAIR_CYCLES=1
PROCEED_TO_MR1=false
PROCEED_TO_MR1_REAPPROVAL=true
```

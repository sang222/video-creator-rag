# MR1 — vận hành production thật

Runbook này áp dụng duy nhất cho PKG1 market revision đã được operator duyệt dưới
ChannelProfileVersion v3. Chạy tuần tự: **MR1-REAPPROVAL chỉ tạo quyền thực thi**,
sau đó **MR1 mới được gọi provider/render/Drive**. Không bước nào cấp quyền upload
hoặc publish YouTube.

## Exact authority

Không resolve bằng `latest`. Trước mỗi lần bắt đầu/resume phải đối chiếu đúng các
ID và SHA-256 sau:

| Authority | Exact ID/ref | Exact version/hash |
|---|---|---|
| Channel | `small-team-ai` | target `US`, locale `en-US` |
| VideoProject | `2522a8f1-1ea4-4d66-8ea5-411aaa8f152b` | `d84682f68160c4bc3d0d27accee417667bb195129bf1bed3519f0d5c2b36f539` |
| PKG1 revision | `a90e2786-f6e0-5480-94a4-fb28fd000edf` | v2 · `b50ff5d3bcbf07de4b709ae0d9017a9df04fec49481fb14c224a709c85b0875b` |
| Package ArtifactVersion | `7de25ac8-46e4-46da-b112-f805f16ebaaa` | `200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd` |
| PKG1 planning approval | `ef766b1d-c1a5-43b8-be98-0751bd055653` | exact package ở trên |
| PKG1 human receipt | `a35c55b8-6887-4e60-a19c-22928205c572` | `24a2d4c7b0dec7394a8b78ab646f66750fbca35282700d50dcde77bd304c2231` |
| ChannelProfileVersion v3 | `d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711` | `1c96bd4dd254ae04f57ba3d7156eb4bc612aeeedc59d2c8e65dda369cd627640` |
| Compiled snapshot của profile v3 | `e6c33d80-f5d8-4f72-9abc-87de3601b89e` | persisted v4 · `12b66551bd9bdfce1d59d1019ff50bc1c49756b6dc4ab505fde080630b4551bc` |
| TargetMarketProfile | `target-market-profile://small-team-ai/v1` | `d456033a947408f671b328f9c5f5589ae86ea4529caf60b18c3d913058d1bb9e` |
| MarketAlignmentDossier | `artifact-version://dba5a8cd-ca61-49e8-b662-c27ad7f02959` | `57b87274528a909db91417071aae687baa60d33ee9aac90731819cf5bbd4c969` |
| DestinationBinding | `destination-binding://small-team-ai/v1` | `411aae66418315da8e6a0bf2cd23e896e89e7cd4827a5b54c36c0437ad63efab` |
| ProviderExecutionPlan | `artifact-version://d0841143-c6bf-4f44-9086-9a8634c22b70` | `4c527232b86cb033c90a93e94d00684c81b7a9559ee0ba3a1a94b9e5cd100f3b` |
| CostEstimateSnapshot | `artifact-version://e6f55187-dbf8-4f4c-9a98-d47ccb645c11` | `eb07c0342506ec1f14f353b19d5c183920b67b3194aa34d1ad9c1156d19ba9aa` |
| RightsDisclosure report | `artifact-version://75f3fc78-3cd4-4c2f-802f-54f9d8603bc3` | `b63b828281c64cea43eef980e18ea40d3e6cf606b625849dd08ffd408bf9942c` |
| Synthetic disclosure draft | `artifact-version://2788551d-4b59-49f3-ae65-c22806fe0139` | `6bb6392e9aa21fee528bab47cc8315f73b683da5adc6cc27259f75504c1cbe36` |
| AssetProvenancePlan | `artifact-version://1aa97323-65e2-43a0-b981-65789b411c26` | `d2672628514d712ff8e84b0cb8300a6f29726eb954044ced23be1e7ec5c0b025` |
| LPRO1 contract | `lpro1.long-form-render-package.v1` | orchestrator `lpro1.long-production-orchestrator/1.0.0` |

MR1 re-approval đã persist thành công và Prompt 2 chỉ nhận đúng authority sau:

| Authority | ID/ref | Hash |
|---|---|---|
| ApprovalDecision | `4ccc7185-e760-4470-aba9-857ab0a18f77` | approval content `4a8c259debc1ae3f94feb7c5be959e0d42bca048911b052a221eda7373d1c25c` |
| MR1 approval ref | `mr1-approval://small-team-ai/2522a8f1-1ea4-4d66-8ea5-411aaa8f152b/a90e2786-f6e0-5480-94a4-fb28fd000edf/v1` | exact single-run scope |
| Approval receipt | `artifact-version://d875858d-46fe-4ce5-a89c-785f266c6b4c` | `749f167ecce6309a42f67330ea829a530468eea5ee332fa662567b0017c4e11d` |
| Readiness preflight | `artifact-version://432f42be-3a17-400a-a97d-2658b05a2ebc` | `57147774df5f982dacc435e0adb622a5471a9f0f9b4cff3dea6ceef402c56bd8` |

Không suy ra authority khác từ approval cũ và không dùng placeholder. Prompt 2
phải fail closed nếu ID/hash ở DB lệch summary này.

Nếu bất kỳ authority/hash nào lệch, dừng trước provider submit. Không sửa business
artifact đã duyệt để làm gate PASS; tạo version/re-approval mới khi cần.

## Ranh giới approval, render và publish

MR1-REAPPROVAL phải kết thúc với provider/render/Drive/YouTube call đều bằng 0.
Approval mới có scope `MR1_REAL_PRODUCTION_EXECUTION`, chỉ dùng cho một run MR1,
và trở thành terminal ngay khi execution bắt đầu. Approval MR1 cũ
`ba688de8-4274-4414-9be1-e8dda827b97e` giữ làm lịch sử `SUPERSEDED`, tuyệt đối
không reuse.

Destination hiện tại là YouTube `@SmallTeamAI`, target `US`,
`PENDING_PLATFORM_ID`, `platform_channel_id=null`, `manual_publish_required=true`.
Áp dụng đúng ranh giới:

| Hành động | Gate |
|---|---|
| MR1 provider execution, local production render và QC | `MR1_RENDER_DESTINATION_GATE=PASS` |
| Drive production archive và checksum/read-back verification | Được phép trong MR1 |
| Tạo ReviewMediaCandidate | Được phép; `not_publishable=true` |
| FinalMediaRef | Chỉ sau Drive VERIFIED và human full-watch PASS |
| Upload card / YouTube upload / publish / PUB1 | `PUBLISH_DESTINATION_GATE=BLOCKED_PENDING_PLATFORM_ID` |

Luôn giữ `UPLOAD_READY=false` và `PUBLISH_EXECUTION_READY=false`. Drive archive
không phải platform upload. Không đổi DestinationBinding thành `VERIFIED`, không
fabricate `platform_channel_id`.

## Attempt caps và cost firewall

Effective caps của exact ProviderExecutionPlan:

| Route | Planned | Maximum approved attempts |
|---|---:|---:|
| ElevenLabs narration | 1 | 1 |
| Forced Alignment | 1 | 1 |
| Pexels `SC-04` | 1 | 1 |
| Pexels `SC-07` | 1 | 1 |
| Pexels `SC-09` | 1 | 1 |
| Gemini Image | 0 scene | **0** |
| Google Veo | 0 scene | **0** |

Native scenes `SC-01/02/03/05/06/08` không có provider attempt. Gemini/Veo có
policy template cap-per-planned-scene trong catalog, nhưng exact package không có
scene được plan cho hai route này; effective authority vì vậy là 0 call.

CostEstimateSnapshot khóa `currency=USD`, `estimated_cost=0.00`,
`hard_cap=1.00`, `actual_cost=null`. `0.00` là estimate theo subscription/free/local
classes, không phải bằng chứng actual billing. Trước mỗi network submit phải mở lại
approval, ledger, request hash, provider/model/voice, idempotency, monthly budget và
hard cap.

Quy tắc bắt buộc:

- SDK automatic retry = false; không retry một attempt đã consumed.
- Không provider switch, không Pexels-to-AI escalation, không external AI-video
  fallback và không route mới.
- Không dùng cap của scene/provider khác. Không gom ba Pexels scene thành reusable
  allowance.
- Duplicate start phải resume đúng durable run/ledger hoặc fail closed; không tạo
  provider call mới.

## Trình tự operator

1. Xác nhận `MR1_REAPPROVAL_FINAL=PASS`, `PROCEED_TO_MR1=true` và exact
   script-generated approval ID/hash. Xác nhận repository/Alembic, package hashes,
   credential booleans, kill switches, toolchain/workspace và Drive readiness.
2. Tạo một fresh MR1 run identity, task authorization, attempt ledger,
   idempotency records, render identity và archive identity; bind toàn bộ exact
   authority ở trên.
3. Submit ElevenLabs đúng một lần; materialize audio/checksum/duration/usage receipt.
4. Chạy Forced Alignment đúng một lần trên exact audio và SpokenTextNormalized;
   yêu cầu strict coverage, không estimated-timing fallback.
5. Compile CanonicalMediaTimeline làm temporal authority duy nhất.
6. Thực thi đúng VisualSourceDecision: Pexels chỉ `SC-04/07/09`; sáu scene còn
   lại dùng native route. Không gọi Gemini Image hoặc Veo.
7. Normalize actual bytes, compile NativeRenderPlan, chạy NativeMotionCompiler và
   NativeFFmpegRenderer; TechnicalMediaQC phải PASS. CreativePerceptualMediaQC có
   thể `PASS` hoặc `REVIEW_REQUIRED` nhưng không thay human authority.
8. Tạo `ReviewMediaCandidate`, tuyệt đối chưa tạo `FinalMediaRef`.
9. Upload complete archive lên Drive bằng một canonical archive identity; chỉ đặt
   `ARCHIVE_VERIFIED=true` sau khi exact item set/count, parent/name, size,
   checksum/read-back và duplicate absence đều PASS.
10. Trình final MP4 tuyệt đối path/hash, thumbnail, captions, local/Drive archive,
    provider counts, estimated/actual cost evidence và mọi `REVIEW_REQUIRED`; dừng
    tại human full-watch.
11. Chỉ khi operator trả `PASS`: persist immutable human receipt bind exact MP4
    hash và Drive receipt, reverify prerequisites, rồi mới tạo production
    `FinalMediaRef`.

Human full-watch không được auto-PASS. Operator phải kiểm tra voice/pacing,
voice-caption sync, caption readability, semantic visual match, Pexels không phải
generic filler, native diagram clarity, niche/US-en-US alignment và tổng thể có thể
publish sau destination closeout. Phản hồi hợp lệ:

```text
PASS
```

hoặc:

```text
REJECT: <reasons>
```

Trước human PASS: `MR1_HUMAN_REVIEW=PENDING`,
`MR1_FINAL=WAITING_HUMAN_REVIEW`, `MR1_FINAL_MEDIA_REF=NOT_CREATED`.

## Recovery không tiêu thụ thêm provider authority

| Tình huống | Hành động duy nhất được phép |
|---|---|
| Lỗi kỹ thuật trước network submit | Sửa nhỏ, re-run preflight/request serialization; attempt chưa consumed. |
| Network submit đã consume nhưng không có usable output | Ghi đủ evidence và dừng `BLOCKED_REQUIRES_NEW_MR1_APPROVAL`; cap hiện tại không cho retry. |
| Provider output đã durable, lỗi timeline/normalize/render/QC | Resume từ stage durable cuối; reuse exact output/checksum, không gọi provider lại. |
| FFmpeg/render lỗi | Rerender local với cùng narration/assets; rerun QC, không recall provider. |
| Drive package/upload/verify lỗi | Sửa và resume cùng media/archive identity; reconcile journal và verify lại, không recall provider. |
| Human reject crop/caption/overlay/motion/transition/readability | Sửa deterministic bằng cùng provider outputs, rerender, QC, reconcile/reverify Drive, full-watch lại. |
| Human reject cần script/narration/provider generation/visual route/metadata/thumbnail mới | Không mutate package; đặt `BLOCKED_REQUIRES_PACKAGE_REVISION_AND_NEW_APPROVAL`. |
| Exact hash/binding mismatch hoặc approval không resolve duy nhất | Fail closed trước submit; không chuyển sang package/approval khác. |

Human full-watch là điểm pause bình thường duy nhất. Attempt đã consumed và hết
authority là hard stop, không phải lý do để bypass ledger. Mọi repair local được
resume từ receipt thành công cuối và phải giữ no-fallback proof.

## Production command và runtime switches

Chỉ chạy command sau khi acceptance suite của Prompt 2 PASS. Command không nhận
approval/package/profile/snapshot từ CLI: toàn bộ exact ID/hash được đóng cứng trong
`scripts/run_mr1_real_production.py`, vì vậy operator không thể vô tình đổi sang
`latest` hoặc một package khác.

Các switch dưới đây chỉ mở đúng provider/render/Drive boundary cho tiến trình MR1.
Giữ upload/publish kill switch đóng và giữ Gemini/Veo tắt vì exact plan có 0 call:

```bash
cd /Users/sangss/Desktop/video-creator-rag

PROVIDER_REAL_EXECUTION_ENABLED=true \
VCOS_PROVIDER_PRODUCTION_EXECUTION_ENABLED=true \
VCOS_DISABLE_MEDIA_PROVIDER_CALLS=false \
ELEVENLABS_REAL_EXECUTION_ENABLED=true \
ELEVENLABS_REAL_GENERATION_ENABLED=true \
ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true \
ELEVENLABS_VOICE_ID=pNInz6obpgDQGcFmaJgB \
ELEVENLABS_MODEL_ID=eleven_multilingual_v2 \
PEXELS_REAL_EXECUTION_ENABLED=true \
PEXELS_REAL_SEARCH_ENABLED=true \
VCOS_GEMINI_IMAGE_REAL_GENERATION_ENABLED=false \
VCOS_VEO_REAL_GENERATION_ENABLED=false \
VCOS_PROVIDER_REAL_READINESS_PROBE_ENABLED=false \
VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED=true \
GOOGLE_DRIVE_OFFLOAD_ENABLED=true \
GOOGLE_DRIVE_ARCHIVE_ENABLED=true \
GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED=true \
VCOS_DISABLE_OLD_PROVIDER_SMOKE=true \
VCOS_DISABLE_UPLOAD_AND_PUBLISH=true \
PYTHONPATH=. .venv/bin/python scripts/run_mr1_real_production.py
```

Runtime gate fail closed nếu credential, OAuth Drive/access token, root folder,
FFmpeg/FFprobe, monthly budgets, exact reapproval summary hoặc canonical DB authority
không PASS. Drive token refresh (nếu token sắp hết hạn) chỉ diễn ra sau mọi
non-network check và exact DB authority PASS; không có billable generation probe.

Command mặc định là idempotent start: nếu exact single-run authority đã tồn tại, nó
resume đúng durable run và không tạo run/provider attempt thứ hai. Có thể resume rõ
ràng cùng run ID đã persist:

```bash
PROVIDER_REAL_EXECUTION_ENABLED=true \
VCOS_PROVIDER_PRODUCTION_EXECUTION_ENABLED=true \
VCOS_DISABLE_MEDIA_PROVIDER_CALLS=false \
ELEVENLABS_REAL_EXECUTION_ENABLED=true \
ELEVENLABS_REAL_GENERATION_ENABLED=true \
ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true \
ELEVENLABS_VOICE_ID=pNInz6obpgDQGcFmaJgB \
ELEVENLABS_MODEL_ID=eleven_multilingual_v2 \
PEXELS_REAL_EXECUTION_ENABLED=true \
PEXELS_REAL_SEARCH_ENABLED=true \
VCOS_GEMINI_IMAGE_REAL_GENERATION_ENABLED=false \
VCOS_VEO_REAL_GENERATION_ENABLED=false \
VCOS_PROVIDER_REAL_READINESS_PROBE_ENABLED=false \
VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED=true \
GOOGLE_DRIVE_OFFLOAD_ENABLED=true \
GOOGLE_DRIVE_ARCHIVE_ENABLED=true \
GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED=true \
VCOS_DISABLE_OLD_PROVIDER_SMOKE=true \
VCOS_DISABLE_UPLOAD_AND_PUBLISH=true \
PYTHONPATH=. .venv/bin/python scripts/run_mr1_real_production.py \
  --resume-run-id <EXACT_MR1_RUN_UUID>
```

Để kiểm tra runtime readiness mà không tạo run/call provider/render/Drive archive:

```bash
# Dùng cùng bộ environment switch ở trên.
PYTHONPATH=. .venv/bin/python scripts/run_mr1_real_production.py --readiness-only
```

Runner ghi nguyên tử ba báo cáo bắt buộc:

- `reports/mr1_real_production_report.md`
- `reports/mr1_summary.json`
- `reports/mr1_repair_cycles.json`

Archive-scoped copy của report/summary/repair cycles phải được local continuation
materialize trước Drive boundary và có mặt trong exact archive manifest. Repo reports
được ghi lại từ durable result sau mỗi start/resume. Không ghi API key, OAuth token,
volatile download URL hoặc Authorization header vào report.

Nếu command trả `MR1_FINAL=WAITING_HUMAN_REVIEW`, kiểm tra các path/ref được in cuối
stdout và thực hiện full-watch. Không chạy lại provider chỉ để sửa FFmpeg/QC/Drive;
resume sẽ reuse exact narration/assets đã durable.

Human closeout không được suy ra từ state và không có auto-PASS. Operator phải truyền
toàn bộ exact bindings; PASS dùng literal `PASS`, REJECT dùng `REJECT: <reason>`:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_mr1_real_production.py \
  --authority-mode sc04 \
  --project-id <EXACT_PROJECT_UUID> \
  --package-artifact-version-id <EXACT_PACKAGE_VERSION_UUID> \
  --package-content-hash <EXACT_PACKAGE_SHA256> \
  --approval-id <EXACT_MR1_APPROVAL_UUID> \
  --approval-content-hash <EXACT_APPROVAL_SHA256> \
  --profile-id <EXACT_PROFILE_UUID> \
  --snapshot-id <EXACT_SNAPSHOT_UUID> \
  --reapproval-summary <EXACT_REAPPROVAL_SUMMARY_PATH> \
  --resume-run-id <EXACT_RUN_UUID> \
  --closeout \
  --human-decision PASS \
  --operator-decision-text PASS \
  --review-media-candidate-artifact-version-id <EXACT_CANDIDATE_VERSION_UUID> \
  --review-media-candidate-content-hash <EXACT_CANDIDATE_SHA256> \
  --reviewed-output-sha256 <ACTUAL_REVIEWED_MP4_SHA256> \
  --drive-archive-receipt-artifact-version-id <EXACT_DRIVE_RECEIPT_VERSION_UUID> \
  --drive-archive-receipt-content-hash <EXACT_DRIVE_RECEIPT_SHA256> \
  --archive-identity <EXACT_ARCHIVE_IDENTITY> \
  --decided-by-user-id <EXACT_OPERATOR_UUID>
```

Closeout readiness chỉ mở lại exact DB/run/file authority và Drive; nó không yêu cầu
hoặc probe ElevenLabs, Pexels, Gemini, Veo hay FFmpeg. Sau PASS, runner giữ canonical
review archive bất biến, live-upload + readback-verify một exact-set finalization
supplement chứa human receipt và final-lineage receipt, rồi mới tạo FinalMediaRef.

Sau human PASS và FinalMediaRef closeout: giữ
`DESTINATION_STATUS=PENDING_PLATFORM_ID`, `UPLOAD_READY=false`,
`PUBLISH_EXECUTION_READY=false`, đặt `PROCEED_TO_DESTINATION_CLOSEOUT=true` và
`PROCEED_TO_PUB1=false`. Không upload YouTube, không bắt đầu PUB1.

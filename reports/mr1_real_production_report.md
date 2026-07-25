# MR1 — báo cáo real production

Thời điểm ghi: `2026-07-25T00:39:20.971064+00:00`. Run: `b932773c-4049-482a-8827-6933d924c34f`.
Trạng thái durable: `BLOCKED_REQUIRES_NEW_MR1_APPROVAL`.

## Exact authority và entry

| Binding | Exact value |
|---|---|
| ApprovalDecision | `f21fb49d-6695-45f1-be2c-231908f3eb93` |
| Approval content hash | `5adbf212e6ac6bea6bf3fde4885e0ff3aa7d40829bfb74643bd709b5690b923c` |
| VideoProject | `0578b24a-1898-443e-99bf-add89d3e61e0` |
| Package ArtifactVersion | `d8471bc0-7d58-4b39-a1f9-267d7b8a02b1` |
| Package content hash | `7d827b7b37a654639383f21c6b6e5cd634c64c68f87832b3b9907dbd4b1fa07c` |
| ChannelProfileVersion v3 | `d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711` |
| Compiled policy snapshot | `e6c33d80-f5d8-4f72-9abc-87de3601b89e` |
| Runtime readiness | `PASS` |

Không resolve `latest`; command chỉ nhận exact authority ở trên. Destination vẫn
`PENDING_PLATFORM_ID`; đây không phải publish authority.

## Provider attempts và cost

| Provider | Operation | Attempt | State |
|---|---|---:|---|
| `elevenlabs` | `elevenlabs:narration` | `1` / `1` | `SUCCEEDED` |
| `forced_alignment` | `elevenlabs:forced_alignment` | `1` / `1` | `SUCCEEDED` |
| `pexels_api` | `pexels:SC-07` | `1` / `1` | `CONSUMED_FAILED` |
| `pexels_api` | `pexels:SC-07:supplement:02` | `1` / `1` | `CONSUMED_FAILED` |
| `pexels_api` | `pexels:SC-09` | `0` / `1` | `PLANNED` |
| `google_drive` | `google_drive:finalization-supplement` | `0` / `1` | `WAITING_HUMAN_PASS` |

Provider logical calls: `4`.
Estimate được duyệt `0.00 USD`, hard cap `1.00 USD`; actual cost chỉ được ghi khi có
evidence, không suy diễn từ subscription/free tier. SDK retry, provider switch,
Pexels-to-AI escalation và external AI-video fallback đều bị cấm.

## Narration, alignment, timeline và assets

| Scene | Exact route | Status | Fallback used |
|---|---|---|---|
| — | — | — | — |

Required provider execution:
`FAIL`. ElevenLabs:
`PASS`; Forced Alignment:
`PASS`; CanonicalMediaTimeline:
`PASS`; asset resolution:
`FAIL`. Gemini Image và Google Veo không
thuộc exact plan và có call count bằng 0.

## Render, QC, review và Drive

| Gate | Result |
|---|---|
| `MR1_ENTRY` | `PASS` |
| `MR1_APPROVAL_BINDING` | `PASS` |
| `MR1_REUSE_DECISIONS` | `PASS` |
| `MR1_PREFLIGHT` | `PASS` |
| `MR1_REQUIRED_PROVIDER_EXECUTION` | `FAIL` |
| `MR1_ELEVENLABS` | `PASS` |
| `MR1_FORCED_ALIGNMENT` | `PASS` |
| `MR1_CANONICAL_TIMELINE` | `PASS` |
| `MR1_PEXELS` | `FAIL` |
| `MR1_GEMINI_IMAGE` | `NOT_REQUIRED` |
| `MR1_GOOGLE_VEO` | `NOT_REQUIRED` |
| `MR1_NATIVE_ASSETS` | `FAIL` |
| `MR1_ASSET_RESOLUTION` | `FAIL` |
| `MR1_MEDIA_NORMALIZATION` | `FAIL` |
| `MR1_NATIVE_RENDER_PLAN` | `FAIL` |
| `MR1_NATIVE_MOTION_COMPILER` | `FAIL` |
| `MR1_NATIVE_FFMPEG_RENDER` | `FAIL` |
| `MR1_TECHNICAL_MEDIA_QC` | `FAIL` |
| `MR1_CREATIVE_MEDIA_QC` | `FAIL` |
| `MR1_REVIEW_MEDIA_CANDIDATE` | `FAIL` |
| `MR1_DRIVE_ARCHIVE` | `FAIL` |
| `ARCHIVE_VERIFIED` | `false` |
| `MR1_PROVIDER_CALL_COUNT` | `4` |
| `MR1_RENDER_ATTEMPTS` | `0` |
| `MR1_REPAIR_CYCLES` | `0` |
| `MR1_HUMAN_REVIEW` | `PENDING` |
| `MR1_FINAL_MEDIA_REF` | `NOT_CREATED` |
| `MR1_FINAL` | `BLOCKED_REQUIRES_NEW_MR1_APPROVAL` |
| `DESTINATION_STATUS` | `PENDING_PLATFORM_ID` |
| `UPLOAD_READY` | `false` |
| `PUBLISH_EXECUTION_READY` | `false` |
| `PROCEED_TO_DESTINATION_CLOSEOUT` | `false` |
| `PROCEED_TO_PUB1` | `false` |

Review MP4: `N/A`  
Thumbnail: `N/A`  
Captions: `N/A`  
Local archive/workspace: `/Users/sangss/Desktop/video-creator-rag/var/mr1/runs/b932773c-4049-482a-8827-6933d924c34f`  
Drive archive ref: `mr1-archive://small-team-ai/b932773c-4049-482a-8827-6933d924c34f`

TechnicalMediaQC phải dùng actual MP4 bytes. Creative QC không thay thế human
full-watch. Trước human PASS chỉ tồn tại `ReviewMediaCandidate`; không tạo
`FinalMediaRef`.

## No-fallback và publish boundary

`provider_substitution_used=false`, `pexels_to_ai_escalation_used=false`,
`youtube_calls=0`. `UPLOAD_READY=false`, `PUBLISH_EXECUTION_READY=false`,
`PROCEED_TO_PUB1=false`. Drive archive không phải YouTube upload.

## Repair cycles và next action

Repair cycles: `0`. Local/render/Drive repair phải
reuse exact provider outputs và archive identity; không consume thêm provider
attempt.

Next action: Giữ nguyên evidence và tạo MR1 approval mới; không retry attempt đã consume.

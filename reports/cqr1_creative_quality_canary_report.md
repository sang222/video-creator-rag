# CQR1 — Creative Quality Canary Report

Ngày: 2026-07-14

Run: `pa1r-cqr1-20260714-paid-canary-001`

```text
purpose=CQR1_CONTROLLED_PAID_CANARY
production_eligible=false
not_publishable=true
```

## Verdict và phạm vi

```text
CQR1D_OFFLINE_FIXTURES=PASS
CQR1D_FINAL_SUITE=PASS
CQR1D_GOLDEN_MEDIA=PASS
CQR1D_TECHNICAL_MEDIA_QC=PASS_OFFLINE_GOLDEN
CQR1D_CREATIVE_MEDIA_QC=PASS_OFFLINE_FIXTURE
CQR1D_PAID_CANARY_PREFLIGHT=BLOCKED
CQR1D_PAID_CANARY=BLOCKED
CQR1D_PAID_TECHNICAL_MEDIA_QC=NOT_CREATED
CQR1D_PAID_CREATIVE_MEDIA_QC=NOT_CREATED
CQR1D_DRIVE_ARCHIVE=BLOCKED
CQR1D_LOCAL_CLEANUP=BLOCKED
CQR1_HUMAN_WATCHABILITY_REVIEW=PENDING
CREATIVE_QUALITY_REPAIR=BLOCKED
FINAL_PRODUCTION_READINESS=NO_GO
provider_call_count=0
provider_probe_count=0
```

Technical/Creative PASS ở trên chỉ áp dụng cho local golden/offline fixture. Paid output chưa tồn tại nên không có paid MediaQC, Drive receipt hoặc human full-watch; offline PASS không được suy diễn thành paid-canary PASS.

## Offline qualification

| Gate | Kết quả |
| --- | --- |
| Alembic heads | `0036_hpr1_veo (head)` |
| `compileall -q app` | `PASS` |
| Required 12-file regression suite | `227 passed, 2 warnings in 115.45s` |
| Focused CQR1-B after bbox/correction hardening | `37 passed in 18.30s` |
| Typed policy/config/compiler suite | `24 passed in 12.63s` |
| `git diff --check` | `PASS` |
| Historical PA1R hashes | `12/12 UNCHANGED` |

`offline_qualification_evidence.json` chứa đúng 10 boolean của `CQR1OfflineQualificationEvidence`, tất cả `true`. Không frontend file nào thay đổi nên không chạy frontend check.

## Durable local golden

Golden purpose là `CQR1_LOCAL_GOLDEN_FIXTURE`, dùng local deterministic alignment/audio carrier, không phải paid canary và không phải human-quality narration sample.

```text
canonical_duration_ms=7770
measured_mp4_duration_ms=7770
av_drift_ms=3
full_decode=true
stream_integrity=true
fast_start=true
NarrationPacingGate=PASS
CaptionCompilationGate=PASS
CaptionLayoutGate=PASS
CaptionSafeAreaGate=PASS
CaptionAudioSyncGate=PASS
CaptionCoverageGate=PASS
TimelineDriftGate=PASS
SceneSemanticMatchGate=PASS
VisualContinuityGate=PASS
AssetAdjacencyGate=PASS
AssetAdjacencyGate_reason=ASSET_ADJACENCY_NOT_APPLICABLE
FinalDurationConsistencyGate=PASS
TechnicalMediaQC=PASS
CreativePerceptualMediaQC=PASS
visual_direction_contract_ref=visual-direction:48d8a2ddfe4bf8eb2091b1e8151d321162d694729bc4228ecf3309c3aaf80050
visual_direction_contract_hash=48d8a2ddfe4bf8eb2091b1e8151d321162d694729bc4228ecf3309c3aaf80050
provider_call_count=0
```

Measured pacing là active/delivered/hook `152.239 / 131.274 / 152.239 WPM`; comma/sentence/section pause `220 / 420 / 650ms`. Caption average/P95 CPS là `14.201 / 14.474`; actual bbox lớn nhất `790x110`, width ratio `0.411458`, bottom margin ratio `0.081481`. Native render plan và compiled manifest cùng bind trực tiếp VisualDirectionContract ref/hash ở trên; visual gate evidence và offline index lặp lại cùng identity. H.264 VideoToolbox, 1920x1080/30, yuv420p/BT.709, AAC 48kHz stereo và Fast Start đều được probe/decode thật. Regression riêng so sánh decoded video/audio `framemd5` qua hai render để chứng minh deterministic essence dù hardware bitstream có thể khác byte.

Evidence nằm tại:

```text
var/tmp/vcos-project-workspaces/pa1r-cqr1-20260714-paid-canary-001/offline-golden/
```

Offline plan cho paid canary cũng đã được compile nhưng chưa execute: English-US script `90` words, target `38.6s`, visible non-production label, provider-neutral VisualDirectionContract, bounded Pexels query plan, Veo prompt và exact 8-second duration-fit decision. Canary content plan ghi `provider_call_count=0`; Veo prompt ghi `provider_call_made=false`. VisualDirection, Pexels query và duration-fit là deterministic transport-free artifacts. `FixedDurationFitDecision.provider_execution_allowed=true` chỉ biểu thị duration fit hợp lệ; global `paid_canary_preflight.provider_execution_allowed=false` vẫn là execution authority và chặn mọi provider call. Candidate ranking, download, TTS, Forced Alignment và Veo submit không chạy khi preflight BLOCKED.

## Safe provider readiness và preflight

```text
PEXELS_API_KEY_CONFIGURED=true
ELEVENLABS_API_KEY_CONFIGURED=true
ELEVENLABS_VOICE_ID_CONFIGURED=false
ELEVENLABS_MODEL_ID_CONFIGURED=false
ELEVENLABS_TTS_ACCESS_CONFIRMED=false
ELEVENLABS_VOICES_READ_CONFIRMED=false
ELEVENLABS_MODELS_READ_CONFIRMED=false
ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=unknown
GEMINI_API_KEY_CONFIGURED=true
GOOGLE_VEO_MODEL_ACCESSIBLE=true
DRIVE_OAUTH_CONNECTED=true
DRIVE_ARCHIVE_ROOT_CONFIGURED=true
secret_values_exposed=false
provider_probe_count=0
offline_gate_passed=true
provider_readiness_passed=false
ledger_fresh=true
provider_execution_allowed=false
provider_call_count=0
```

Preflight block reason là thiếu ElevenLabs voice/model configuration và chưa xác nhận TTS, Voices Read, Models Read, Forced Alignment. Fresh ledger giữ bảy operation ở `PLANNED`; mọi `attempt_count=0`, `provider_call_made=false`. Không provider callback nào được truyền vào offline rehearsal.

Exact operator action:

```text
Grant ElevenLabs Text to Speech Access, Voices Read, Models Read, and Forced Alignment Access; configure ELEVENLABS_VOICE_ID/ELEVENLABS_MODEL_ID; then set ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true before any provider probe.
```

Sau action này phải chạy lại preflight. Không tự động bắt đầu paid canary và không có second paid attempt/retry/fallback.

## Artifact state

Workspace:

```text
/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-cqr1-20260714-paid-canary-001
```

| Artifact | Path/ref | State |
| --- | --- | --- |
| Offline golden MP4 | `offline-golden/runs/cqr1-local-golden-001/nr1_smoke.mp4` | `CREATED_NON_PRODUCTION_FIXTURE` |
| Offline golden contact sheet | `offline-golden/render/proxy/cqr1-offline-golden-contact-sheet.jpg` | `CREATED` |
| Offline TechnicalMediaQC | `offline-golden/qc/technical_media_qc.json` | `PASS` |
| Offline CreativePerceptualMediaQC | `offline-golden/qc/creative_perceptual_media_qc.json` | `PASS` |
| Final paid canary MP4 | `render/final/cqr1-non-production-canary.mp4` | `NOT_CREATED` |
| Paid contact sheet | `render/proxy/cqr1-contact-sheet.jpg` | `NOT_CREATED` |
| Paid TechnicalMediaQC | `qc/technical-media-qc.json` | `NOT_CREATED` |
| Paid CreativePerceptualMediaQC | `qc/creative-perceptual-media-qc.json` | `NOT_CREATED` |
| Before/after packet | `manifests/before_after_comparison.json` | `CREATED_PARTIAL_BLOCKED_PRECANARY` |
| Human packet | `qc/human_watchability_review_packet.json` | `CREATED_PENDING` |
| Drive archive plan | `manifests/drive_archive_plan.json` | `NOT_ATTEMPTED_PREFLIGHT_BLOCKED` |
| Drive archive receipt | `manifests/drive_archive_receipt.json` | `NOT_CREATED` |
| Required Drive path | `smoke_tests/2026-07-14/cqr1/pa1r-cqr1-20260714-paid-canary-001/` | `NOT_CREATED` |

Before/after packet giữ PA1R nguyên trạng, đánh dấu `acceptance_complete=false` và `NON_EQUIVALENT_DIAGNOSTIC_ONLY`. Paid metrics đều `null` với reason `PAID_CANARY_PREFLIGHT_BLOCKED`; không giả lập paid comparison.

## Archive, cleanup, human và no-publish

```text
archive_state=NOT_ATTEMPTED_PREFLIGHT_BLOCKED
archive_attempt_count=0
archive_receipt_created=false
purge_count=0
cleanup_eligible=false
human_watchability_review=PENDING
```

Không Drive upload/verification nên cleanup tiếp tục BLOCKED. Codex không đánh dấu human PASS. Không YouTube write/upload, `FinalMediaRef`, `HumanUploadTask`, `UploadedVideo`, production promotion, auto-publish, learning promotion hoặc CH1-FLEX.

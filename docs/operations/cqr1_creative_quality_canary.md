# CQR1 creative-quality canary operations

## Current technical terminal state

```text
run_id=pa1r-cqr1-20260716-paid-canary-009
purpose=CQR1_CONTROLLED_PAID_CANARY
technical_state=COMPLETE
archive_state=VERIFIED
human_review=PASS
production_eligible=false
not_publishable=true
```

Run 009 là fresh successor, không phải retry. Technical execution đã hoàn tất; operator đã full-watch 1× và approve. `PROCEED_TO_CH1_FLEX=true` chỉ mở gate cho một task riêng, không tự khởi chạy task đó.

## Immutable lineage

- Run 002: nguồn TTS thật, 72 words, 38.220s.
- Run 004: nguồn Forced Alignment và `VerifiedNarrationAlignment` thật.
- Run 007: nguồn Pexels/Veo thật; dừng do BT.709 VUI normalization cục bộ.
- Run 008: provider attempts `0`; render/Technical QC PASS nhưng dừng post-render do dùng sai `CanonicalCaptionCue.end_ms`.
- Run 009: sửa thành `caption_end_ms`, dùng fresh workspace/approval/ledger/idempotency, rồi hoàn tất render/archive.

Mỗi source workspace được kiểm tra inventory hash và copy-pin theo SHA-256. Không sửa source run hay reuse provider attempt ngầm.

## Run 009 ledger contract

| Operation | State | Max attempts | Attempt count | Provider call |
| --- | --- | ---: | ---: | --- |
| ElevenLabs TTS | `REUSED` | 0 | 0 | false |
| ElevenLabs Forced Alignment | `REUSED` | 0 | 0 | false |
| Pexels search | `REUSED` | 0 | 0 | false |
| Pexels download | `REUSED` | 0 | 0 | false |
| Google Veo submit | `REUSED` | 0 | 0 | false |
| Google Veo output | `REUSED` | 0 | 0 | false |
| Drive archive | `SUCCEEDED` | 1 | 1 | true |

New media-provider call paths bị hard-block. Không retry, fallback, YouTube, production promotion hoặc CH1-FLEX.

## Preflight rules

Trước local downstream hoặc external archive phải PASS:

- Alembic đúng một head `0036_hpr1_veo`.
- `compileall -q app`.
- Exact 12-file CQR1 regression suite.
- `git diff --check`.
- Historical PA1R hashes unchanged.
- TTS/alignment/visual source inventory và artifact hashes unchanged.
- Voice `pNInz6obpgDQGcFmaJgB`, model `eleven_multilingual_v2`, speed `0.90`.
- Drive OAuth/root/quota ready.
- Ledger binding đúng: sáu media operations `REUSED/max_attempts=0`, Drive `PLANNED/max_attempts=1`.

Preflight run 009 PASS với `provider_call_count=0`; execution-tree hash `8960901782eeb1981507b087261f78dd1ce645f9082c1ec1aabeb2ec7532b663`.

## Canonical timing and render rules

- Audio authority: immutable ElevenLabs final audio, `38,220ms`.
- Timing authority: immutable verified Forced Alignment, coverage `1.0`, missing/extra `0/0`.
- Captions và scenes chỉ lấy timing từ `CanonicalMediaTimeline`.
- Không estimated timing, parallel SRT timeline, fixed final `-t` hoặc atempo correction.
- VideoToolbox H.264 outputs bắt buộc bitstream metadata: `colour_primaries=1`, `transfer_characteristics=1`, `matrix_coefficients=1`.
- Final output: 1920×1080, 30fps, H.264/yuv420p/BT.709, AAC 48k stereo, burned captions, fast start.

Run 009 final duration evidence:

```text
canonical=38220ms
narration=38220ms
caption_end=38220ms
scene_end=38220ms
final_mp4=38220ms
max_drift=0ms
```

## Gate interpretation

`TechnicalMediaQC=PASS` không tự động có nghĩa creative/human PASS.

Run 009 machine gates:

- Caption compilation/safe-area/sync/coverage/drift: PASS.
- Scene semantic/continuity/adjacency: PASS.
- Narration pacing: REVIEW_REQUIRED.
- Caption layout: REVIEW_REQUIRED.
- `CreativePerceptualMediaQC=REVIEW_REQUIRED`.

Reason codes phải đưa vào human packet: `PACE_SLOW_REVIEW`, `PACE_SECTION_PAUSE_SHORT`, `CAPTION_READING_SPEED_REVIEW`, `CAPTION_DURATION_REVIEW`.

## Drive archive and cleanup

Archive verified tại:

```text
smoke_tests/2026-07-16/cqr1/pa1r-cqr1-20260716-paid-canary-009
```

Receipt yêu cầu mọi file có local/Drive size bằng nhau và checksum verified. Run 009: 130/130 files, total `32,752,597` bytes, receipt hash `b33029cf14a6fecf4075026badb144ef26bf38c0b5f8d79f58a778cf96f325b9`.

Chỉ sau `archive_state=VERIFIED` mới cleanup. Cleanup là `PARTIAL`: xóa archive staging, normalized media và render scratch; giữ final MP4, contact sheet, manifests, QC/reports và review packet.

## Human handoff

Operator đã xem full video ở tốc độ 1×, chấm tám dimension đều `4/5` (tổng `32/40`) và khai báo không có critical issue. Receipt append-only nằm tại `var/tmp/vcos-project-workspaces/pa1r-cqr1-20260716-paid-canary-009/manifests/human_watchability_review_receipt.json`; blank pre-review packet vẫn bất biến.

Final gate:

```text
CQR1_HUMAN_WATCHABILITY_REVIEW=PASS
CREATIVE_QUALITY_REPAIR=PASS
FINAL_PRODUCTION_READINESS=GO
PROCEED_TO_CH1_FLEX=true
```

Human approval không cấp quyền YouTube write/publish, production promotion, `FinalMediaRef`, `HumanUploadTask` hoặc `UploadedVideo`. Không re-upload Drive; verified technical archive vẫn giữ nguyên.

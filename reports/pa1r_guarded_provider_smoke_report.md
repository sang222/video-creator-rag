# PA1R — Guarded Provider Smoke Report

Ngày: 2026-07-13

Run: `pa1r-20260713-guarded-smoke-005`

Mode: `RESUME_FROM_VERIFIED_UPSTREAM_ARTIFACTS`
Kết quả cuối: technical PASS và operator human review `PASS`; `PROCEED_TO_CH1_FLEX=true`.

## Preflight và reused upstream

AS1/HPR1, Runtime LTS, ProviderStackDriftGuard, Creatomate/Luma absence, Alembic current/head `0036_hpr1_veo`, FFmpeg/ffprobe, workspace/disk/path đều PASS.

Credential booleans only:

```text
GEMINI_API_KEY_CONFIGURED=true
DRIVE_OAUTH_CONNECTED=true
DRIVE_ARCHIVE_ROOT_CONFIGURED=true
SECRET_VALUES_EXPOSED=false
```

Run `-004` không bị sửa. Hai input được copy vào workspace mới sau checksum và provenance validation:

```text
Pexels sha256=dfe525c7c23666fc52827aea9d35e7bc1caaa8106818105057e9d1b72e443088
Pexels new search/download calls=0/0
ElevenLabs sha256=8fa1dce1d7b94bdd6a2385abff63bd7305068886275fc530050e80d9d9005ab5
ElevenLabs new generation calls=0
source_run_id=pa1r-20260713-guarded-smoke-004
reuse_validation=CHECKSUM_AND_PROVENANCE
```

Approval `operator-approval-pa1r-20260713-guarded-smoke-005`, fresh Veo idempotency key và planned ledger tồn tại trước submit. Veo estimate `0.80 USD`, hard cap `1.00 USD`; provider không trả actual billed amount.

## Google Veo real verification

Đúng một submit tới `veo-3.1-fast-generate-preview`; operation `models/veo-3.1-fast-generate-preview/operations/e7494wjnxdap` hoàn tất sau 6 bounded polls.

```text
transport=GEMINI_API_NATIVE
generate_audio_parameter_sent=false
person_generation_sent=allow_all
domain_character_policy=NO_CHARACTER
prompt/negative-prompt safeguards=PASS
generation_submit_count=1
automatic_retry=false
output_count=1
output_size_bytes=6398633
output_sha256=c437f6179ce4016037e123c749c452455fa1272b86775cdf0aa129ae470d342a
```

Provider output có một AAC stereo 48 kHz stream. Evidence ghi đúng `provider_audio_present=true`, `provider_audio_stream_count=1`, policy `DISCARD`; normalization `-an` tạo `normalized_veo_audio_stream_count=0`.

## Normalization, render và QC

Pexels, Veo và ElevenLabs đều có MediaNormalizationManifest. Timeline: `0–7 / 7–13 / 13–21 / 21–25`.

Render đầu tiên dừng local vì `/opt/homebrew/bin/ffmpeg` thiếu `drawtext`. Root cause được giữ tại `local_render_failure_evidence.json`; targeted repair chọn canonical `ffmpeg-full`. `resume-downstream` chỉ dùng Veo output đã verified, không poll/download/submit provider lần hai.

NativeFFmpeg render PASS:

```text
final=/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-20260713-guarded-smoke-005/render/final/pa1r-provider-smoke.mp4
size_bytes=14083544
sha256=3d53771db9753423e1f0eb7ab7d9c66154ccb1ad606d0d5d608ae571db813d62
duration_seconds=25.0
```

MediaQC PASS: complete decode, MP4/H.264/AAC, 1920x1080, 30 fps, yuv420p, BT.709, AAC 48 kHz stereo, Fast Start atom order, duration/A-V structural sync, narration completeness, compiled captions/label, Pexels/Veo scene refs và provider-audio removal đều PASS. Readability, pronunciation, unintended-freeze và visual-policy judgment vẫn thuộc human review.

## Drive, cleanup, duplicate và no-publish

Drive archive:

```text
path=smoke_tests/2026-07-13/pa1r/pa1r-20260713-guarded-smoke-005
folder_id=1NwH5-lwkESp3-ZLDscmusxm4wjK5b5lr
files=37
local_bytes=40677315
remote_bytes=40677315
all_file_ids_present=true
all_size_sha256_verified=true
archive_state=VERIFIED
```

Cleanup: `LOCAL_CLEANUP_PARTIAL_REVIEW_OUTPUT_RETAINED`, reclaimed `19050558` bytes; final MP4, proxy, contact sheet, manifests/QC retained; `.part` count `0`; run `-004` untouched.

Duplicate-check:

```text
new_pexels_search=0
new_pexels_download=0
new_elevenlabs_generation=0
second_veo_generation_submit=0
second_drive_archive=0
```

YouTube calls, FinalMediaRef, HumanUploadTask, UploadedVideo và learning promotion đều `0`; frozen DB invariants unchanged. Persistent execution/production flags false, upload/publish kill switch enabled, `.env` unchanged.

## P0/P1/P2/P3 và next action

- P0: none.
- P1: none open.
- P2 resolved: PA1R render binary thiếu `drawtext`; chuyển sang canonical `ffmpeg-full`, downstream resume không provider call.
- P2 human-review items: operator reviewed and accepted.
- P3: none.

Operator approval được ghi nhận lúc `2026-07-13T23:21:15+07:00`: `PA1R_HUMAN_REVIEW=PASS`, `PA1R_FINAL=PASS`, `PROCEED_TO_CH1_FLEX=true`.

Exact next action: CH1-FLEX có thể bắt đầu bằng một task riêng khi operator yêu cầu. Approval này không cấp quyền YouTube write/publish.

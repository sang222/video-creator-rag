# PA1R — Guarded Real Provider Smoke Report

Ngày: 2026-07-13
Latest run: `pa1r-20260713-guarded-smoke-004`
Kết quả: `FAIL` tại Google Veo submit; downstream dừng fail-closed.

## Preflight

Entry conditions PASS trước media execution:

- AS1/HPR1, Runtime LTS và ProviderStackDriftGuard PASS;
- Creatomate/Luma runtime absent;
- Alembic head `0036_hpr1_veo`;
- FFmpeg/ffprobe, workspace/disk/path guards PASS;
- required entry regressions `121/121` PASS;
- `compileall` và `git diff --check` PASS.

Sau compatibility repair, final required regressions `122/122` PASS; Alembic head, compileall và diff-check tiếp tục PASS.

Credential booleans only:

```text
PEXELS_API_KEY_CONFIGURED=true
ELEVENLABS_API_KEY_CONFIGURED=true
GEMINI_API_KEY_CONFIGURED=true
DRIVE_OAUTH_CONNECTED=true
DRIVE_ARCHIVE_ROOT_CONFIGURED=true
SECRET_VALUES_EXPOSED=false
```

Readiness PASS: ElevenLabs còn 130.797 characters; Veo model `veo-3.1-fast-generate-preview` accessible với action `predictLongRunning`; Pexels credential ready; Drive còn 13.495.437.550 bytes. Estimate tổng `0.867091 USD` dưới hard cap `3.00 USD`.

Approval được bind riêng vào run:

```text
approval_ref=operator-chat-pa1r-approval://pa1r-20260713-guarded-smoke-004
max_pexels_search=1
max_pexels_download=1
max_elevenlabs_generation=1
max_veo_generation=1
automatic_retry=false
production_eligible=false
not_publishable=true
```

Paid-attempt, provider-boundary, monthly-budget, idempotency, global/provider kill-switch và fresh planned-ledger gates đều PASS; tất cả attempt bằng 0 trước execute.

## Pexels

Một search flow `/v1/videos/search` và một MP4 download PASS:

- asset/file ID `32150707` / `13707650`;
- creator `Usman AbdulrasheedGambo`;
- source `https://www.pexels.com/video/professional-video-editing-setup-overview-32150707/`;
- MP4 1920x1080, 9 giây, 5.596.770 bytes;
- HTTP 200, `video/mp4`, redirect count `0`;
- SHA-256 `dfe525c7c23666fc52827aea9d35e7bc1caaa8106818105057e9d1b72e443088`;
- media request headers chỉ gồm `Accept`, `User-Agent`;
- `.part` được probe/fsync/atomic rename; raw media URL không persist.

## ElevenLabs

Đúng một narration PASS:

- voice `Adam - Dominant, Firm`, ID `pNInz6obpgDQGcFmaJgB`;
- model `eleven_multilingual_v2`;
- 369 input characters; text hash `4320f9f22ece14f59ad0d87561a0382ad0b9ddebd583ed2547ac6ff69761a3ac`;
- duration `24.102313s`, dưới giới hạn 25s;
- 386.656 bytes; SHA-256 `8fa1dce1d7b94bdd6a2385abff63bd7305068886275fc530050e80d9d9005ab5`;
- structural audio QC PASS; understandability/pronunciation human review PENDING;
- provider response không cung cấp actual USD amount.

## Google Veo blocker

Đúng một submit attempt đã tới Gemini API và trả terminal HTTP 400 trước operation ID:

```text
reason_code=VEO_PERSON_GENERATION_VALUE_UNSUPPORTED
http_status=400
provider_status=INVALID_ARGUMENT
message=dont_allow for personGeneration is currently not supported
generation_submit_attempt_count=1
provider_operation_id=null
output_count=0
automatic_retry=false
actual_cost_usd=null
actual_cost_reason=INVALID_ARGUMENT_BEFORE_OPERATION_ID; provider billing evidence unavailable
```

Root cause: Veo 3.1 text-to-video chỉ hỗ trợ `personGeneration=allow_all`; `dont_allow` là lựa chọn của Veo 2. Đây là giới hạn được ghi trong [Google Veo 3.1 Gemini API documentation](https://ai.google.dev/gemini-api/docs/video).

Targeted offline repair đổi transport field thành `allow_all`. Domain request vẫn `character_policy_mode=NO_CHARACTER`, prompt và negative prompt vẫn cấm people/faces/presenter; output bắt buộc qua review/QC, nên transport compatibility không mở quyền tạo presenter. Fake-client regression xác nhận `generate_audio=None`, `person_generation=allow_all`, audio policy `DISCARD` và domain `NO_CHARACTER`. Focused tests `50/50` PASS, không provider call. Patch chưa được real-verified.

## Downstream, idempotency và no-publish

Không có Veo output nên normalization, provider-audio inspection/removal, NativeFFmpeg render, MediaQC và Drive archive không chạy. Không tạo final MP4/contact sheet/Drive receipt; cleanup stage BLOCKED, purge count `0`. Pexels và ElevenLabs outputs được giữ để audit; không có `.part` file.

Duplicate-check tạo zero new calls:

```text
second_pexels_search=0
second_pexels_download=0
second_elevenlabs_generation=0
second_veo_generation_submit=0
second_drive_archive=0
```

DB invariants không đổi. FinalMediaRef, HumanUploadTask, UploadedVideo, ProviderJobSnapshot và LearningToMemoryPromotionRun đều `0`; YouTube call count `0`; frozen context không mutation. Mọi artifact `production_eligible=false`, `not_publishable=true`.

Persistent flags sau run: provider/Pexels/ElevenLabs/Veo/Drive/local-render flags false; production flags false; media calls và upload/publish disabled.

## P0/P1/P2/P3

- P0: none.
- P1 resolved offline, not real-verified: Veo 3.1 text-to-video transport phải dùng `personGeneration=allow_all`.
- P2: Veo output/audio, render, MediaQC và Drive chưa được đánh giá do fail-closed.
- P3: none.

## Exact next action và CH1-FLEX

Không retry hoặc mutate `pa1r-20260713-guarded-smoke-004`. Real verification kế tiếp cần run ID mới (đề xuất `pa1r-20260713-guarded-smoke-005`), fresh approval/idempotency/planned ledger. `PROCEED_TO_CH1_FLEX=false` cho tới khi technical smoke hoàn tất và operator xem final MP4.

# PA1R Guarded Provider Smoke

PA1R là one-shot, non-production và fail-closed. Entry order:

1. local invariants, Alembic, drift guard, workspace/disk và credential booleans;
2. read-only billing/quota/model probes;
3. đúng một Pexels search và một MP4 download;
4. đúng một ElevenLabs narration;
5. đúng một Veo 8s submit, bounded poll và một output download;
6. local normalization, NativeFFmpeg render, MediaQC;
7. Drive upload/read-back/checksum verification, rồi partial cleanup;
8. duplicate-check không gọi provider lần hai.

CLI:

```bash
PYTHONPATH=. .venv/bin/python tools/pa1r/run_pa1r.py preflight

VCOS_DISABLE_MEDIA_PROVIDER_CALLS=false \
VCOS_PROVIDER_REAL_EXECUTION_ENABLED=true \
PEXELS_REAL_EXECUTION_ENABLED=true \
PEXELS_REAL_SEARCH_ENABLED=true \
ELEVENLABS_REAL_EXECUTION_ENABLED=true \
ELEVENLABS_REAL_GENERATION_ENABLED=true \
VCOS_VEO_REAL_GENERATION_ENABLED=true \
VCOS_PA1R_VEO_SMOKE_ENABLED=true \
GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED=true \
VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED=true \
PYTHONPATH=. .venv/bin/python tools/pa1r/run_pa1r.py execute

PYTHONPATH=. .venv/bin/python tools/pa1r/run_pa1r.py duplicate-check
```

Không ghi các flag trên vào `.env`. Production execution và upload/publish luôn false/disabled. Không có provider retry, AI-video failover, dashboard action hay YouTube write.

Veo contract cố định: `veo-3.1-fast-generate-preview`, 8s, 720p, 16:9, một output, `NO_CHARACTER`, audio `DISCARD`. Narration authority là ElevenLabs; final mix authority là NativeFFmpeg. Mọi output có `production_eligible=false`, `not_publishable=true`.

Với transport Gemini Developer API, không truyền `GenerateVideosConfig.generate_audio`. Veo 3.1 tạo audio mặc định; field này là control riêng của Enterprise Agent Platform và SDK sẽ reject nếu xuất hiện. VCOS vẫn probe audio output thực tế, ghi nhận có/không, rồi normalize bằng `-an` theo policy `DISCARD`.

Veo 3.1 text-to-video chỉ chấp nhận `personGeneration=allow_all`; `dont_allow` chỉ khả dụng cho Veo 2. VCOS dùng giá trị transport bắt buộc này nhưng vẫn giữ domain policy `NO_CHARACTER` qua approved prompt, negative prompt và output review/QC boundary. Giá trị transport không phải quyền cho phép presenter hoặc human likeness trong artifact.

Run `pa1r-20260712-guarded-smoke-001` dừng tại Pexels search HTTP 403; provider-side root cause không được evidence cũ capture đầy đủ. Search headers sau đó được sửa và real-verified trong run `pa1r-20260712-guarded-smoke-002`: search PASS, deterministic selection chọn asset `32150707`, nhưng media MP4 download trả HTTP 403. Boundary cũ không capture redirect/content-type/host/query lifecycle nên nguyên nhân media-side cụ thể vẫn chưa xác định. PEXELS-DL1 sửa và test offline boundary này; không retry hai run đã tiêu thụ. Cần explicit operator approval và run_id mới để real-verify trước khi gọi downstream providers.

## PEXELS-DL1 media boundary

Pexels API search và media download là hai HTTP boundaries tách biệt. Search gửi API credential tới `api.pexels.com`; media client chỉ gửi `Accept` và `User-Agent`, không bao giờ nhận API credential hoặc cookie.

`PexelsDownloadPlan` là durable và chỉ chứa provider IDs, source/creator metadata, rendition shape, `volatile://` reference, full URL SHA-256, expected host và query-present boolean. Raw `video_files[].link` chỉ nằm trong `PexelsDownloadExecutionContext` ở process memory, không serialize và bị expire sau đúng một attempt. `volatile://` không phải execution URL.

Media client giữ nguyên raw URL đầu vào, tự follow redirect có giới hạn, validate HTTPS/public IP trước mỗi hop, không log target URL, stream vào `target.mp4.part`, áp byte cap, tính SHA-256, fsync, ffprobe rồi mới atomic rename. HTTP/content/media failure xóa `.part`, không tạo success receipt và chỉ lưu host/status/content-type/count/reason evidence đã redacted.

PEXELS-DL1 là repair offline. Không được dùng task này để retry run `-002`; PA1R kế tiếp bắt buộc có run ID, idempotency keys, ledger và operator approval mới.

## Run 2026-07-13 (`pa1r-20260713-guarded-smoke-003`)

Preflight, Pexels search/download và ElevenLabs narration PASS. Veo consume đúng một submit attempt nhưng Google Gen AI SDK 2.10.0 reject trước operation ID vì adapter truyền Enterprise-only `GenerateVideosConfig.generate_audio` qua Gemini Developer API. Không retry; render/MediaQC/Drive dừng fail-closed.

Adapter đã bỏ field này khỏi Gemini Developer API config và thêm fake-client regression; provider audio vẫn được coi là expected/always-on và phải probe rồi remove bằng `-an` khi có output. Repair chỉ được test offline. Không reuse run `-003`; lần real-verify kế tiếp cần run ID, ledger, idempotency keys và explicit approval mới.

Run `pa1r-20260713-guarded-smoke-004` real-verified việc bỏ `generate_audio`, nhưng Gemini API tiếp tục reject `personGeneration=dont_allow` bằng HTTP 400 vì Veo 3.1 text-to-video chỉ hỗ trợ `allow_all`. Run dừng không retry. Adapter đã chuyển sang giá trị transport hợp lệ `allow_all` trong khi giữ `NO_CHARACTER` ở prompt/negative-prompt/output-review boundary; patch này chưa được real-verified.

## Resume run 2026-07-13 (`pa1r-20260713-guarded-smoke-005`)

Run `-005` dùng mode `RESUME_FROM_VERIFIED_UPSTREAM_ARTIFACTS`. Runner riêng `tools/pa1r/run_pa1r_resume.py` validate checksum/provenance và copy Pexels/ElevenLabs từ run `-004`; nó không import hoặc gọi Pexels/ElevenLabs clients. Ledger mới chỉ plan một Veo generation và một Drive archive.

Preflight:

```bash
VCOS_PA1R_RUN_ID=pa1r-20260713-guarded-smoke-005 \
PYTHONPATH=. .venv/bin/python tools/pa1r/run_pa1r_resume.py preflight
```

One-shot execute:

```bash
VCOS_PA1R_RUN_ID=pa1r-20260713-guarded-smoke-005 \
VCOS_DISABLE_MEDIA_PROVIDER_CALLS=false \
VCOS_PROVIDER_REAL_EXECUTION_ENABLED=true \
VCOS_VEO_REAL_GENERATION_ENABLED=true \
VCOS_PA1R_VEO_SMOKE_ENABLED=true \
GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED=true \
VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED=true \
PYTHONPATH=. .venv/bin/python tools/pa1r/run_pa1r_resume.py execute
```

Transport evidence phải lấy từ config thực mà adapter submit: `generate_audio=None`, `person_generation=allow_all`; domain request vẫn `NO_CHARACTER`. Nếu operation còn processing, chỉ dùng `resume-poll`; không chạy `execute` lần hai.

Run này real-verified compatibility repair: đúng một submit được accepted, operation `models/veo-3.1-fast-generate-preview/operations/e7494wjnxdap` SUCCEEDED, một output được download. Provider output có một AAC stream; normalization `-an` loại bỏ stream và final narration chỉ lấy từ ElevenLabs.

Local render đầu tiên phát hiện `/opt/homebrew/bin/ffmpeg` thiếu `drawtext`. Đây là local binary-selection issue sau provider success, không phải Veo failure. Repair chuyển PA1R sang `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`; mode `resume-downstream` xác thực operation/download receipt và checksum trước khi render lại, không poll/download/submit Veo.

```bash
# Chỉ dùng khi Veo output của cùng run đã SUCCEEDED và download receipt/checksum hợp lệ.
<same one-shot flags> PYTHONPATH=. .venv/bin/python \
  tools/pa1r/run_pa1r_resume.py resume-downstream

VCOS_PA1R_RUN_ID=pa1r-20260713-guarded-smoke-005 \
PYTHONPATH=. .venv/bin/python tools/pa1r/run_pa1r_resume.py duplicate-check
```

Kết quả kỹ thuật: NativeFFmpeg/MediaQC PASS; Drive path `smoke_tests/2026-07-13/pa1r/pa1r-20260713-guarded-smoke-005` VERIFIED 37/37 files; cleanup partial giữ final/proxy/contact sheet; duplicate-check zero new calls.

Operator đã xem MP4 và explicit approve lúc `2026-07-13T23:21:15+07:00`: `PA1R_HUMAN_REVIEW=PASS`, `PA1R_FINAL=PASS`, `PROCEED_TO_CH1_FLEX=true`. Trạng thái này chỉ mở gate sang task CH1-FLEX riêng; không cấp quyền YouTube write/publish.

## Post-run creative-quality repair state

Historical PA1R run evidence above remains immutable. A later creative review found subtitle scale, narration pace, visual continuity and narration/caption synchronization defects. Current repaired-program state therefore treats infrastructure as PASS but production readiness as NO_GO until CQR1-A/B/C/D complete.

CQR1-A replaces the hard-coded `0–7 / 7–13 / 13–21 / 21–25` production pattern for new repaired paths with verified final-audio timing and `CanonicalMediaTimeline`. It does not rerun or relabel PA1R, call a provider, render, archive or authorize CH1-FLEX. See `docs/architecture/canonical_media_timeline.md` and `docs/operations/narration_timing_and_alignment.md`.

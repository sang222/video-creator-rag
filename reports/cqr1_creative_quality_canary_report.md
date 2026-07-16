# CQR1 — Creative Quality Canary Report

Ngày thực thi: 2026-07-16
Run hoàn tất: `pa1r-cqr1-20260716-paid-canary-009`

```text
purpose=CQR1_CONTROLLED_PAID_CANARY
production_eligible=false
not_publishable=true
```

## Kết quả

```text
CQR1_PAID_CANARY_RESUME=PASS
CQR1D_PAID_CANARY_PREFLIGHT=PASS
CQR1D_ELEVENLABS_TTS=PASS
CQR1D_FORCED_ALIGNMENT=PASS
CQR1D_PEXELS=PASS
CQR1D_GOOGLE_VEO=PASS

CQR1B_NARRATION_PACING=REVIEW_REQUIRED
CQR1B_CAPTION_COMPILATION=PASS
CQR1B_CAPTION_LAYOUT=REVIEW_REQUIRED
CQR1B_CAPTION_SAFE_AREA=PASS
CQR1B_CAPTION_AUDIO_SYNC=PASS
CQR1B_CAPTION_COVERAGE=PASS
CQR1B_TIMELINE_DRIFT=PASS

CQR1C_SCENE_SEMANTIC_MATCH=PASS
CQR1C_VISUAL_CONTINUITY=PASS
CQR1C_ASSET_ADJACENCY=PASS

CQR1D_PAID_TECHNICAL_MEDIA_QC=PASS
CQR1D_PAID_CREATIVE_MEDIA_QC=REVIEW_REQUIRED
CQR1D_DRIVE_ARCHIVE=PASS
CQR1D_LOCAL_CLEANUP=PARTIAL

CQR1_HUMAN_WATCHABILITY_REVIEW=PASS
CREATIVE_QUALITY_REPAIR=PASS
FINAL_PRODUCTION_READINESS=GO
PROCEED_TO_CH1_FLEX=true
```

## Lineage và one-shot safety

Run 008 đã render được MP4/Technical QC nhưng dừng fail-closed ở bước duration evidence do code đọc `CanonicalCaptionCue.end_ms` thay vì `caption_end_ms`. Lỗi xảy ra sau render nhưng trước Drive; provider calls/attempts của run 008 là `0/0`. Run 008 được đóng immutable và không resume.

Run 009 dùng workspace, approval, ledger và idempotency mới. Nguồn real provider được reuse theo hash:

| Operation | Source | Run 009 mode | Call mới |
| --- | --- | --- | ---: |
| ElevenLabs TTS | run 002 | `REUSED_IMMUTABLE_RUN_002_OUTPUT` | 0 |
| ElevenLabs Forced Alignment | run 004 | `REUSED_IMMUTABLE_RUN_004_VERIFIED_ALIGNMENT` | 0 |
| Pexels search/download | run 007 | `REUSED_IMMUTABLE_RUN_007_OUTPUT` | 0 |
| Google Veo submit/output | run 007 | `REUSED_IMMUTABLE_RUN_007_OUTPUT` | 0 |
| Drive archive | run 009 | `SUCCEEDED/VERIFIED` | 1 |

Run 009 có tổng `provider_call_count=1`, chỉ là Drive archive. Không retry, fallback hoặc YouTube call.

## Offline và paid evidence

| Scope | TechnicalMediaQC | CreativePerceptualMediaQC | Ý nghĩa |
| --- | --- | --- | --- |
| Offline fixture/golden | `PASS` | `PASS` | Qualification local |
| Real paid-canary assets + render 009 | `PASS` | `REVIEW_REQUIRED` | Technical complete; chờ human full-watch |
| Drive archive | `VERIFIED` | — | 130/130 files khớp size/checksum/metadata |
| Human review | — | `PASS` | Operator full-watch 1×; 8×`4/5`; total `32/40`; critical issues none |

Không suy diễn paid verdict từ offline fixtures.

## Narration, alignment và canonical timing

| Evidence | Giá trị |
| --- | --- |
| Voice/model/speed | `pNInz6obpgDQGcFmaJgB` / `eleven_multilingual_v2` / `0.90` |
| Spoken words | `72` |
| Audio duration | `38,220ms` — trong range `28–40s` |
| Audio SHA-256 | `2c6a9382fee10783ebfe5c5a2e33b6dbb16b2cd3a253d19f8729aa7a96de6fb6` |
| Spoken token coverage | `1.0`; missing `0`; extra `0` |
| Canonical timeline | 6 scenes; `38,220ms` |
| Final audio/caption/scene/MP4 end | `38,220ms` cho cả bốn |
| Maximum duration drift | `0ms` |

Measured pacing:

```text
active_speech_wpm=128.526
delivered_wpm=113.030
hook_first_8s_active_wpm=130.169
comma_pause_ms_median=220.5
sentence_pause_ms_median=574.5
section_pause_ms_median=499.0
```

`NarrationPacingGate=REVIEW_REQUIRED` với `PACE_SLOW_REVIEW` và `PACE_SECTION_PAUSE_SHORT`; không có speed-up/atempo/regeneration.

## Captions và visual quality

Caption compilation, safe area, audio sync, coverage và drift đều PASS. Real libass bbox evidence có 10 cues, 1–2 dòng, bottom margin ratio `0.081481`, maximum block-width ratio `0.447917`, CPS trung bình `13.917`, p95 `16.955`. Layout là `REVIEW_REQUIRED` do `CAPTION_DURATION_REVIEW` và `CAPTION_READING_SPEED_REVIEW`.

Visual gates trên real Pexels/Veo frames:

```text
SceneSemanticMatchGate=PASS score=0.88
VisualContinuityGate=PASS score=0.74
AssetAdjacencyGate=PASS score=0.72
native_bridge_between_provider_assets=true
direct_pexels_to_veo_cut=false
```

## Render và QC

Final MP4:

```text
/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-cqr1-20260716-paid-canary-009/render/final/cqr1-non-production-canary.mp4
sha256=8d96eb944d5034967016d66e7d83cf4042270aeb5b0d6646f2c525b4a430b25d
duration=38.220s
video=H.264 1920x1080 30fps yuv420p BT.709
audio=AAC 48kHz stereo
captions=burned-in
```

`TechnicalMediaQC=PASS`. `CreativePerceptualMediaQC=REVIEW_REQUIRED` chỉ tổng hợp bốn reason code pacing/layout ở trên; không có gate `BLOCK`.

## Before/after packet

`acceptance_complete=true`. So với PA1R immutable `pa1r-20260713-guarded-smoke-005`:

| Metric | PA1R | Run 009 |
| --- | ---: | ---: |
| Delivered WPM | `129.448` estimate | `113.030` measured |
| End drift | `897.687ms` | `0ms` |
| Caption block-width ratio | `0.8198` | max `0.447917` |
| Caption font scale | unavailable | `0.047` |
| CPS | `6.429–11.500` | avg `13.917`, p95 `16.955` |
| Semantic/continuity/adjacency | unavailable | `0.88 / 0.74 / 0.72` |

Side-by-side: `/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-cqr1-20260716-paid-canary-009/comparison/side-by-side-pa1r-vs-real-paid-canary.jpg`.

## Drive và cleanup

```text
archive_state=VERIFIED
path=smoke_tests/2026-07-16/cqr1/pa1r-cqr1-20260716-paid-canary-009
verified_files=130/130
local_bytes=32752597
drive_bytes=32752597
receipt_hash=b33029cf14a6fecf4075026badb144ef26bf38c0b5f8d79f58a778cf96f325b9
```

Partial cleanup thu hồi `51,696,274` bytes, không có deletion failure. Final MP4, contact sheet, manifests, QC và human-review packet được giữ lại; `purge_count=0`.

## Human verdict

Operator đã full-watch ở tốc độ 1× và explicit approve lúc `2026-07-17T04:31:39+07:00`: tám dimension đều `4/5`, tổng `32/40`, không critical issue. Receipt: `/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-cqr1-20260716-paid-canary-009/manifests/human_watchability_review_receipt.json`.

`PROCEED_TO_CH1_FLEX=true` chỉ mở gate cho task CH1-FLEX riêng; task này không khởi chạy CH1-FLEX, production promotion, `FinalMediaRef`/`HumanUploadTask`/`UploadedVideo` hoặc YouTube publish.

# CQR1 — Human Watchability Review

Ngày: 2026-07-14

```text
run_id=pa1r-cqr1-20260714-paid-canary-001
state=PENDING
production_eligible=false
not_publishable=true
uninterrupted_full_watch_1x=NOT_STARTED
provider_call_count=0
```

Human review chưa thể bắt đầu vì paid canary preflight đang `BLOCKED` và final
media chưa được tạo. Mọi score và issue field dưới đây phải do operator điền sau
một lần xem trọn vẹn, không gián đoạn ở tốc độ 1.0x. Codex không được đánh dấu
review này PASS.

## Planned review artifacts

Workspace dự kiến:

```text
/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-cqr1-20260714-paid-canary-001
```

| Evidence | Planned path/ref | Current state |
| --- | --- | --- |
| Final MP4 | `render/final/cqr1-non-production-canary.mp4` | `NOT_CREATED` |
| Contact sheet | `render/proxy/cqr1-contact-sheet.jpg` | `NOT_CREATED` |
| Before/after packet | `manifests/before_after_comparison.json` | `CREATED_PARTIAL_BLOCKED_PRECANARY` |
| Structured pending packet | `qc/human_watchability_review_packet.json` | `CREATED_PENDING` |
| Drive receipt | `manifests/drive_archive_receipt.json` | `NOT_CREATED` |
| Drive archive | `smoke_tests/2026-07-14/cqr1/pa1r-cqr1-20260714-paid-canary-001/` | `NOT_CREATED` |

## Full-watch protocol

- [ ] Xem toàn bộ final MP4 một lần, không gián đoạn, ở `1.0x`.
- [ ] Ghi timestamp cho từng vấn đề quan sát được.
- [ ] Chỉ dùng `0.75x` để spot-check các timestamp đã đánh dấu.
- [ ] Xác nhận contact sheet, before/after packet và Drive receipt khớp đúng run.

Full watch chưa thể bắt đầu vì paid MP4, paid contact sheet và Drive receipt đều
`NOT_CREATED`. Partial before/after packet chỉ so PA1R với local synthetic
golden ở chế độ `NON_EQUIVALENT_DIAGNOSTIC_ONLY`; nó không hoàn thành acceptance
và không thay thế paid media review.

## Scores 1–5

| Dimension | Score (1–5) | Notes |
| --- | ---: | --- |
| Voice naturalness |  |  |
| Voice pace comfort |  |  |
| Caption readability |  |  |
| Caption sync trust |  |  |
| Scene relevance |  |  |
| Visual continuity |  |  |
| Transition quality |  |  |
| Overall watchability / AI-slop perception |  |  |
| **Total / 40** |  |  |

Outcome policy:

- `PASS`: total tối thiểu 32/40, không dimension nào dưới 3 và không critical issue.
- `REPAIR_REQUIRED`: total 24–31 hoặc defect không critical lặp lại.
- `REJECT`: critical sync/pace/readability/semantic defect hoặc overall watchability dưới 3.
- Critical issue luôn override điểm trung bình.

## Timestamped issues

| Timestamp (`HH:MM:SS.mmm`) | Reason code | Observation | Severity | Repair recommendation |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

## Critical reason-code checklist

Các ô đang để trống/chưa đánh giá; không được hiểu là đã xác nhận `false`.

- [ ] `HUMAN_VOICE_RUSHED`
- [ ] `HUMAN_VOICE_UNNATURAL`
- [ ] `HUMAN_CAPTION_DOMINANT`
- [ ] `HUMAN_CAPTION_UNREADABLE`
- [ ] `HUMAN_SYNC_DISTRACTING`
- [ ] `HUMAN_SCENE_IRRELEVANT`
- [ ] `HUMAN_VISUAL_DISCONTINUITY`
- [ ] `HUMAN_TRANSITION_JOLT`
- [ ] `HUMAN_AI_SLOP`

## Blocking action trước review

```text
Grant ElevenLabs Text to Speech Access, Voices Read, Models Read, and Forced Alignment Access; configure ELEVENLABS_VOICE_ID/ELEVENLABS_MODEL_ID; then set ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true before any provider probe.
```

Sau action này phải chạy lại paid preflight; review vẫn `PENDING` cho đến khi
operator xem final MP4 thực tế và điền đầy đủ evidence.

No-publish: không YouTube write/upload, `FinalMediaRef`, `HumanUploadTask`,
`UploadedVideo`, production promotion, auto-publish, learning promotion hoặc
CH1-FLEX.

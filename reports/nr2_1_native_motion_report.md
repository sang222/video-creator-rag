# NR2.1 — NativeMotionPack visibility and strategy differentiation

Date: 2026-07-11. Local-only, non-production.

## Verdict

```txt
NR2_1_TECHNICAL=PASS
NR2_1_MOTION_SHOWCASE=PASS
NR2_1_STRATEGY_A=PASS
NR2_1_STRATEGY_B=PASS
NR2_1_STRATEGY_C=REVIEW_REQUIRED
NR2_1_HUMAN_REVIEW=PENDING
NR2_SELECTED_STRATEGY=NONE
NR2_FINAL=WAITING_HUMAN_REVIEW
PROCEED_TO_FIRST_CHANNEL_PILOT=false
```

## Existing NR2 audit

NR2 cũ ghi một animation preset ở cấp strategy nhưng compiled scenes chỉ chứa role/timing; không có requested/compiled preset, parameter, fallback hoặc evidence ref theo scene. Filtergraph là composition tĩnh. Vì vậy motion coverage có thể audit được là 0/7 cho A/B/C; HOLD_STATIC/default count không thể chứng minh; khác biệt chủ yếu đến từ asset-role/color. Đây là nguyên nhân animation không rõ.

## Scene motion decisions

Mọi scene dài 12 giây; animation 0–12 giây trong scene; transition `fade_soft` 500 ms, scene cuối `cut`; fallback=false. Evidence: `motion_manifest_[a|b|c].json` và audit proxy tương ứng.

| Strategy | Scene | Treatment | Compiled preset | Parameters / HOLD reason | Expected movement |
| --- | --- | --- | --- | --- | --- |
| A | s01_hook | NATIVE | fact_card_pop | intensity .45 | card entry |
| A | s02_problem | NATIVE | pushin_slow | zoom 2.5% | restrained push-in |
| A | s03_scenario | NATIVE | data_card_hold | data_readability | stable metric |
| A | s04_pattern | NATIVE | timeline_step_reveal | intensity .45 | step reveal |
| A | s05_cost | NATIVE | hold_static | caption_heavy_section | none, intentional |
| A | s06_scale | NATIVE | comparison_reveal | intensity .45 | directional reveal |
| A | s07_example | SUPPORTING | hold_static | pacing_rest | none, intentional |
| B | s01_hook | NATIVE | kenburns_center_soft | zoom 4% | centered zoom |
| B | s02_problem | NATIVE | pushin_slow | zoom 3.5% | push-in |
| B | s03_scenario | NATIVE | data_card_hold | data_readability | stable metric |
| B | s04_pattern | NATIVE | timeline_step_reveal | intensity .45 | step reveal |
| B | s05_cost | SUPPORTING | pan_left_slow | pan 5% | left pan |
| B | s06_scale | SUPPORTING | pan_right_slow | pan 5% | right pan |
| B | s07_example | HERO | cta_card_fadeup | intensity .45 | payoff rise |
| C | s01_hook | NATIVE | kenburns_subject_left | zoom 5.5% | cinematic zoom |
| C | s02_problem | NATIVE | pushin_slow | zoom 5% | stronger push-in |
| C | s03_scenario | SUPPORTING | pan_left_slow | pan 7% | strong left pan |
| C | s04_pattern | SUPPORTING | pan_right_slow | pan 7% | strong right pan |
| C | s05_cost | HERO | kenburns_center_soft | zoom 6% | cinematic zoom |
| C | s06_scale | HERO | kenburns_subject_left | zoom 6% | cinematic zoom |
| C | s07_example | HERO | cta_card_fadeup | intensity .45 | payoff rise |

Requested animation type is the uppercase semantic equivalent of compiled preset. Transition-in is `cut` for first scene, otherwise `fade_soft`; transition-out as above. No compiler fallback was used.

| Metric | A | B | C |
| --- | ---: | ---: | ---: |
| Scenes / explicit decisions | 7 / 7 | 7 / 7 | 7 / 7 |
| Defaulted HOLD_STATIC | 0 | 0 | 0 |
| Intentional HOLD_STATIC | 2 | 0 | 0 |
| Visible-motion coverage | 57.14% | 85.71% | 100% |
| Average zoom delta | 2.5% | 3.75% | 5.63% |
| Average pan displacement | 0% | 5% | 7% |
| Average transition | 500 ms | 500 ms | 500 ms |
| Fallback/default | 0 | 0 | 0 |

Preset usage counts and exact decision hashes are in motion manifests. `MotionStrategyDifferentiationGate=PASS`; A uses explanatory reveals/holds, B adds controlled pan/zoom, C uses continuous stronger cinematic grammar.

## Showcase and outputs

`nr2_native_motion_pack_showcase.mp4` is 92 seconds and demonstrates 23 required transition, still/native, card/UI and overlay presets for 4 seconds each with review-only key/duration/intensity/threshold labels. MediaQC PASS, 12.150 seconds render.

Post-review correction: the first NR2.1 render used `drawbox` expressions that were effectively static. A direct 49s/51s frame check produced identical hashes, so that output was rejected and rerendered with time-evaluated foreground streams (`overlay eval=frame`, `scale eval=frame`, opacity fades). The corrected showcase frame hashes at the same timestamps are `ea3d8217...e95c` and `7bce7aac...a90d`, proving visible frame change. All seven outputs and receipts were replaced by the corrected render set.

All six strategy outputs are 84 seconds, same script/audio/SRT/timing hashes as NR2, H.264 VideoToolbox 1080p30 with AAC and burned captions. Clean outputs contain no `MOTION AUDIT` overlay; proxy filtergraphs do. MediaQC PASS for all; elapsed 11.13–11.27 seconds.

## Gates and boundaries

A/B: completeness, visibility, fallback audit, overload and narrative alignment PASS. C: completeness/visibility/fallback PASS; `MotionOverloadGate=REVIEW_REQUIRED` and `MotionNarrativeAlignmentGate=REVIEW_REQUIRED` because all scenes move and quantified/mechanism scenes use cinematic pan rather than stable data treatment.

No ElevenLabs, Luma, Pexels, Drive, YouTube, network media, production job, provider ledger, FinalMediaRef, HumanUploadTask, contract/context mutation, learning promotion, 4K or ProRes occurred. No commit/tag.

Next checkpoint: human reviews showcase, clean outputs and proxies, then explicitly selects A/B/C/HYBRID/REJECT_ALL. First-channel pilot remains blocked while pending.

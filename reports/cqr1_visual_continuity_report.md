# CQR1-C — Visual Direction and Continuity

Date: 2026-07-14. Scope: provider-neutral contracts, deterministic local planning/ranking, prompt compilation and offline fixture gates. Không thực hiện provider call, paid canary, production render, human full-watch hay Drive archive.

## Verdict

```text
CQR1C_TYPED_POLICY_SNAPSHOT=PASS
CQR1C_VISUAL_DIRECTION_CONTRACT=PASS
CQR1C_PEXELS_CONTEXTUAL_PLAN=PASS
CQR1C_PEXELS_DETERMINISTIC_RANKING=PASS
CQR1C_VEO_PROMPT_COMPILER=PASS
CQR1C_VEO_FIXED_DURATION_FIT=PASS
CQR1C_REQUIRED_POLICY_INJECTION=PASS
CQR1C_SCENE_SEMANTIC_MATCH_GATE=PASS_OFFLINE_FIXTURE
CQR1C_VISUAL_CONTINUITY_GATE=PASS_OFFLINE_FIXTURE
CQR1C_ASSET_ADJACENCY_GATE=PASS_OFFLINE_FIXTURE
CQR1C_PROVIDER_CALL_COUNT=0
CQR1C_OFFLINE_IMPLEMENTATION=PASS
CQR1C_REAL_PROVIDER_VERIFICATION=NOT_RUN
```

`PASS_OFFLINE_FIXTURE` xác nhận implementation và ngưỡng deterministic bằng fixture; đây không phải đánh giá chất lượng media thật hoặc human approval.

## Typed policy snapshot

`creative_quality_policy_catalog.yaml` chứa đúng bảy family cho `small-team-ai`: narration pacing, caption style, caption sync, visual language, visual continuity, creative MediaQC và human watchability. Registry parse inner payload qua typed B/C/D models thay vì chỉ kiểm tra `dict`; malformed threshold bị block. `visual_continuity_policy` cũng sở hữu typed ranking weights, explicit risk penalties và Veo duration-fit thresholds; các C service không còn fallback về channel threshold/default constant.

`ChannelProfileCompiler` chiếu policy vào compiled snapshot mới với `policy_ref`, `policy_hash`, `catalog_version` và `catalog_hash`. Test xác nhận `profile_input`, `profile_input_hash` và channel metadata không đổi. Các service chọn policy theo channel key được inject; không có constant hoặc branch hard-code `small-team-ai`.

## VisualDirectionContract

Contract provider-neutral cố định realism/treatment, human presence, environment/industry, time/lighting/palette, contrast/saturation, camera/lens/movement, motion/framing/DOF/grain/tone, prohibited clichés, identity markers và adjacent-scene constraints. Contract mang format-identity ref/hash, visual-strategy ref/hash và deterministic `content_hash`; không chứa Pexels, Google hoặc Veo field.

| CQR1-C gate | Operating point | Offline status |
| --- | --- | --- |
| `SceneSemanticMatchGate` | PASS `>=0.78`; REVIEW `>=0.68`; BLOCK `<0.68` | PASS |
| `VisualContinuityGate` | PASS `>=0.70`; REVIEW `>=0.58`; BLOCK `<0.58` | PASS |
| `AssetAdjacencyGate` | minimum previous/next score theo ngưỡng continuity | PASS |
| Hard-conflict override | logo/brand, NO_CHARACTER, fake UI evidence, endorsement, strong temperature conflict, camera jolt, motion conflict | PASS |
| Cross-provider cut | semantic PASS và adjacency PASS đều bắt buộc | PASS |

Borderline luôn trả `REVIEW_REQUIRED`. Hard conflict block độc lập với điểm tổng. Evidence lưu scene/asset refs, semantic/direction/adjacency scores, hard-conflict reasons, ranking, rationale, representative still refs, gate results và hash.

## Pexels contextual planning and ranking

`PexelsQueryPlanner` nhận scene semantics, visual-direction ref/hash, narration-derived target duration, aspect ratio, crop safety, locale, bounded `per_page`, previous/next summaries và reuse history. Legacy AS1 query path vẫn giữ tương thích; contextual path không gọi adapter/network.

Deterministic rank score:

| Signal | Weight |
| --- | ---: |
| semantic relevance | 0.36 |
| visual-direction fit | 0.18 |
| previous-scene continuity | 0.12 |
| next-scene continuity | 0.08 |
| crop safety | 0.08 |
| motion suitability | 0.07 |
| technical quality | 0.05 |
| originality bonus | 0.03 |

Sau weighted sum, risk policy inject lần lượt `0.03` mỗi prior use (cap `0.12`), `0.08` exact reuse, `0.02` cho từng unknown logo/person/brand field, `0.04` identifiable person và total cap `0.30`. Semantic/continuity gates vẫn độc lập: fixture “visually attractive but unrelated” có raw score cao hơn nhưng không được chọn khi semantic `<0.68`; ứng viên relevant/style-matched hợp lệ phía sau được chọn deterministic. Logo, trademark, missing source metadata, prohibited cliché và explicit hard-conflict tags bị reject trước ranking. Ranking manifest lưu cả injected weights, penalties và score thresholds.

## Veo prompt and fixed-duration contract

`VeoPromptCompiler` chỉ compile prompt, không sở hữu SDK/transport. Anatomy ổn định theo thứ tự: subject/action; environment/industry; realism/treatment; lighting/time; camera angle/shot size; camera movement; framing/focal style; motion intensity; continuity hint; negative constraints. Prompt hash và content hash deterministic; `NO_CHARACTER`, text/logo/watermark/fake-interface cùng prohibited clichés được đưa vào negative constraints.

Downstream request contract vẫn là:

```text
transport=GEMINI_DEVELOPER_API
model=veo-3.1-fast-generate-preview
duration=8 seconds
resolution=720p
aspect_ratio=16:9
output_count=1
generate_audio omitted/None
person_generation=allow_all
provider_audio_policy=DISCARD
```

Contract trên chỉ được kiểm tra bằng code/regression; CQR1-C không submit request. Duration-fit policy được inject từ catalog: approved output `8s`, exact tolerance `0.25s`, bridge tối đa `2s`, minimum useful trim `4s`; decision artifact lưu chính threshold snapshot. Duration fit luôn phục tùng narration-derived scene timing: 8s dùng một asset; scene ngắn hơn trim cân đối quanh action peak; mismatch nhỏ dùng native/Pexels bridge; mismatch lớn replan trước provider execution. Narration timing, playback speed và looping không được thay đổi.

## Offline verification

```text
CQR1-C + typed policy + config focused: 29 passed in 14.02s
AS1 legacy regression: 19 passed, 1 dependency warning in 8.88s
compileall: PASS
git diff --check: PASS
```

Fixtures cover deterministic contract/hash, typed policy parsing, contextual query inputs, exact ranking weights, independent semantic/hard gates, borderline review, cross-provider adjacency, stable prompt anatomy, negative constraints và 8-second fit decisions. Existing AS1 asset acquisition and provider-boundary tests remain green.

## Explicit non-claims

```text
provider_call_made=false
pexels_search_or_download_call=0
veo_submit_or_output_call=0
paid_canary=NOT_RUN
human_full_watch=NOT_RUN
drive_upload_or_archive_verification=NOT_RUN
production_render=NOT_RUN
youtube_write=0
production_eligible=false
```

Report này không claim real Pexels relevance, real Veo visual quality, paid execution success, human watchability PASS hoặc Drive verification. Các bước đó thuộc guarded CQR1-D entry gate và approval riêng.

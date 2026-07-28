# SC-09 Visual Route and Query Review

## Kết luận

`SC09_ROUTE_VERDICT=SC09_PEXELS_ROUTE_INVALID`

`SC09_QUERY_VERDICT=REJECT_PEXELS_ROUTE`

`SC09_PREFERRED_SOURCE_ROUTE=NATIVE_DIAGRAM`

`SC09_REVISED_ROUTE_TECHNICAL_PREFLIGHT=PASS`

Revised query không được submit. Ledger vẫn `attempt_count=0`,
`search_submit_count=0`, `download_submit_count=0`,
`network_submit_started=false`, `state=PLANNED`, `submit_state=NOT_SUBMITTED`,
`request_hash=null`.

## Exact context

- Package artifact version `d8471bc0-7d58-4b39-a1f9-267d7b8a02b1`, hash
  `7d827b7b...`.
- Script artifact version `4c0ac729-32c5-4005-9078-013b399e8802`,
  `/segments/8`, `S09`, segment hash `0a97f9b1...`.
- SceneVisualIntent `b0e8b068-b79b-4854-81b7-15e68df0992f`,
  `/scenes/8`, hash `55ad4f47...`.
- VisualDirectionContract `24a1ca16-cdaa-4b2e-ba4a-158613dcd267`, hash
  `e62c2141...`.
- VisualSourceDecision `658e43ed-8c8d-43f9-968d-234e41215d99`,
  `/decisions/8`, hash `3e29f3d7...`.
- Unsubmitted-attempt proof `c98da711-f2a2-4806-85e0-1f53b9f09276`,
  hash `5db5127d...`.

Scene window `396380–449260 ms`; stock context window cũ
`396380–404380 ms`; native explanation `404380–449260 ms`.

## Meaning authority

Narration yêu cầu một action plan có cấu trúc:

- chọn một handoff lặp lại;
- khai báo `TRIGGER`, `INPUTS`, `OWNER`, `SUCCESS_CONDITION`,
  `EXCEPTION_PATH`;
- đo current baseline trước khi build;
- dùng twenty-hour example chỉ để test assumption;
- chạy bounded pilot, luôn hiển thị fallback;
- quyết định stop/continue theo observed result, không theo headline.

Đây là `process/conclusion_action_plan`. Meaning phụ thuộc exact five-field
structure, thứ tự baseline → pilot → result và quan hệ fallback/stop. Clip người
làm việc trong office không mang được meaning đó.

## Independent feature assessment

| Feature | Giá trị |
|---|---:|
| filmability | 0.25 |
| stock searchability | 0.20 |
| required specificity | 0.92 |
| custom composition | 0.65 |
| exact text | 0.88 |
| exact number | 0.70 |
| named workflow nodes | true |
| diagram clarity advantage | 0.93 |
| evidence truth | 0.45 |
| identity consistency | 0.05 |
| human action | 0.05 |
| motion semantic value | 0.62 |

Motion giúp reveal/pacing nhưng không phải điều kiện để hiểu quan hệ; vì
`0.62 < 0.70`, `NATIVE_DIAGRAM` phù hợp hơn motion graphic. Exact labels/numbers
và named nodes hard-block Pexels theo VSR1. Không cần authorized UI/product
evidence và không có brand/product dependency.

## Revised query review

Query family đã được human-review:

1. `people working together office workplace b roll`
2. `people working together office close up action`
3. `people working together office clean composition`

Authority hash `95554e73...`, plan hash `08d7762f...`.

Query dùng observable stock vocabulary và tránh abstract words, nhưng thất bại
ở route-level fidelity:

- subject là generic people; action chỉ là generic working;
- office environment không encode five audit fields;
- không rank được trigger/input/owner/success/exception;
- không encode baseline, bounded pilot, visible fallback hay observed result;
- US small-business context không thể được chứng minh chỉ từ query;
- kết quả khả dĩ là generic office filler, không nối được narrative progression.

Vì route bị reject, không tạo query family thay thế và không package-bind query
cũ. Kết luận không dựa trên việc attempt còn trống.

## Alternative route spec

Native diagram gồm:

- primary audit card: `TRIGGER`, `INPUTS`, `OWNER`, `SUCCESS_CONDITION`,
  `EXCEPTION_PATH`;
- supporting nodes: `CURRENT_BASELINE`, `BOUNDED_PILOT`, `VISIBLE_FALLBACK`,
  `OBSERVED_RESULT`, `STOP_OR_CONTINUE`;
- edges: fields define one handoff; baseline precedes build; pilot stays
  bounded; fallback stays visible; observed result drives stop/continue.

Timing: `0–18s` five fields; `18–28s` baseline + twenty-hour assumption marker;
`28–40s` bounded pilot + fallback; `40–52.88s` result → stop/continue. Diagram
vẫn đọc được khi freeze frame; motion chỉ reveal opacity/focus, không đổi
meaning. Text/number authority là native-only; không fake UI, generated
text/number/logo, stock layer hay fallback.

## Offline gates và zero-call proof

Completeness, Pexels prohibition, DiagramSuitability `NATIVE_DIAGRAM`,
EvidenceTruth `NOT_REQUIRED`, niche, market, semantic spec fixture, continuity,
repetitive-risk, rights và zero-provider-cost đều `PASS`.

Requirements hash `5fa06e28...`; decision hash `fc3c5cff...`. Provider calls
`4 → 4`; render, Drive, YouTube đều `0`. Chi tiết exact lineage, query receipt,
features và gate matrix nằm trong
`reports/sc09_visual_route_review_summary.json`.

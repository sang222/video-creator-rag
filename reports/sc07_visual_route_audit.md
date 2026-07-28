# SC-07 Visual Route Audit

## Kết luận

`SC07_ROUTE_VERDICT=PEXELS_ROUTE_INVALID`

`SC07_PREFERRED_SOURCE_ROUTE=NATIVE_MOTION_GRAPHIC`

`SC07_REVISED_ROUTE_TECHNICAL_PREFLIGHT=PASS`

Audit chỉ đọc repository evidence, không submit provider, không download, không sửa
run/package/approval/ledger cũ. Snapshot đầu vào:
`2484ace36230a3dc9c6cfe93ee46db40dab8767d04b46b4950f605c7bc4ebba9`.

## Entry và lineage

- Run: `b932773c-4049-482a-8827-6933d924c34f`;
  `MR1_FINAL=BLOCKED_REQUIRES_NEW_MR1_APPROVAL`.
- VideoProject: `0578b24a-1898-443e-99bf-add89d3e61e0`, revision
  `88fa9f76-99e8-5ec5-8cdd-63c836031bac` v3, hash
  `0115137e...`.
- Package: artifact version `d8471bc0-7d58-4b39-a1f9-267d7b8a02b1`,
  hash `7d827b7b...`.
- Script: artifact version `4c0ac729-32c5-4005-9078-013b399e8802`,
  `/segments/6`, `S07`, segment hash `c2ba49c0...`.
- SceneVisualIntent: `b0e8b068-b79b-4854-81b7-15e68df0992f`,
  `/scenes/6`, hash `55ad4f47...`.
- VisualDirectionContract: `24a1ca16-cdaa-4b2e-ba4a-158613dcd267`, hash
  `e62c2141...`.
- VisualSourceDecision: `658e43ed-8c8d-43f9-968d-234e41215d99`,
  `/decisions/6`, hash `3e29f3d7...`.
- VisualPlan `7186e7ad...`; CompiledAssetRequestPlan `ea2724c5...`;
  ProviderExecutionPlan `9557bd18...`; CostEstimateSnapshot `d241fd38...`.
- TargetMarketProfile `target-market-profile://small-team-ai/v1`;
  NicheAlignmentDossier `7f9381e8...`; MarketAlignmentDossier `dba5a8cd...`.

Entry gates đã resolve đúng version/hash: `VSR1`, `IMG1`, `VQC1`, `LPRO1`,
`GEO1`, `GEO2`, `CH1_MARKET_V3`, `PKG1_MARKET_REVISION` đều `PASS`;
`PRODUCTION_PACKAGE_APPROVED=true`.

## Meaning authority

Window SC-07 là `292050–345360 ms`; supporting-stock window cũ chỉ
`292050–300050 ms`, sau đó native explanation `300050–345360 ms`.
Narration yêu cầu người xem hiểu:

- normal path không đủ;
- missing data, duplicate request, unusual approval và outage phải branch đến
  explicit destination;
- original input phải được giữ;
- exception phải có reason code và named owner;
- ngưỡng exception điều khiển pause;
- manual fallback phải luôn nhìn thấy.

Đây là `mechanism/primary_explanation`, không phải cảnh “people discussing
paperwork”. Meaning phụ thuộc quan hệ, trình tự, label và state change. Camera
thật có thể quay người họp nhưng không thể chụp chính xác cơ chế. Một clip đẹp
vẫn semantically hollow; motion có giá trị vì branch/enqueue/assign/pause là
meaning.

## Feature reassessment

| Feature | Giá trị |
|---|---:|
| filmability | 0.35 |
| stock searchability | 0.25 |
| required specificity | 0.90 |
| custom composition | 0.82 |
| exact text | 0.76 |
| exact number | 0.35 |
| named workflow nodes | true |
| diagram clarity advantage | 0.95 |
| evidence truth | 0.40 |
| identity consistency | 0.10 |
| human action | 0.25 |
| motion semantic value | 0.86 |

Bound package không có numeric VSR1 feature vector cho scene này; nó chỉ persist
route `PEXELS_VIDEO`, role `PEXELS_SUPPORTING`, observable sub-intent
`People discussing office paperwork together.`, eligibility
`PEXELS_SUPPORTING_ONLY`, fallback class `PEXELS_ONLY`; policy alternative
`PEXELS_PHOTO` không được runtime-authorize. Audit không coi thiếu vector là
thiếu scene authority: narration và approved intent đủ để reassess. Khác biệt
vật chất là reassess toàn scene thay vì chỉ context window 8 giây.

VSR1 thresholds giữ nguyên: filmability/searchability Pexels `>=0.70`;
custom composition `<=0.30`; diagram advantage `>=0.60`; native-motion
semantic value `>=0.70`; semantic selection threshold `0.78`.

## Pexels failure

Evidence:
`pexels-search-ranking-failure-c91f13fab9d4-6a305fc90759e883.json`,
SHA-256 `1ebb69f9...`.

- Search `1`; returned `20`; technically valid `19`.
- Candidate `8170595` bị loại vì `7s < 8s`.
- Mười chín candidate còn lại đạt technical viability nhưng semantic scores:
  một candidate `0.60`, ba candidate `0.40`, mười lăm candidate `0.25`.
- Best `5941021` chỉ match `discussing/office/paperwork`; thiếu exception,
  owner, preserved input, reason code, pause và fallback.
- Không download, không selection, không usable output.

Typed failure factors:

`QUERY_MISREPRESENTS_SCENE_MEANING`,
`STOCK_LIBRARY_CANNOT_EXPRESS_MECHANISM`,
`CANDIDATES_MATCH_ENVIRONMENT_NOT_ACTION`,
`CANDIDATES_MATCH_ACTION_NOT_NARRATIVE_PURPOSE`,
`SCENE_REQUIRES_AUTHORED_COMPOSITION`,
`SCENE_REQUIRES_NATIVE_DIAGRAM`.

`19 technically valid` chỉ chứng minh duration/rendition; không chứng minh route,
semantic, market, niche hay continuity.

## Route spec

Route mới là native-only, không stock layer:

1. `NORMAL_PATH`: luồng ổn định trái sang phải.
2. `EXCEPTION_BRANCH`: bốn card
   `MISSING_DATA/DUPLICATE_REQUEST/UNUSUAL_APPROVAL/SYSTEM_OUTAGE` rời luồng.
3. `CONTROL_QUEUE`: mỗi card giữ `ORIGINAL_INPUT`, nhận `REASON_CODE`, đến
   `NAMED_OWNER`.
4. `DECISION`: reveal `RESUME/PAUSE_PILOT/MANUAL_FALLBACK`.

Timing: `0–8s` normal path + first branch; `8–31s` bốn exception; `31–44s`
reason/owner/input; `44–53.31s` pause/fallback. Text và số dùng native authority,
title-safe/caption-safe. Cấm fake UI, generated text/number/logo và runtime
fallback.

## Offline gates và zero-call proof

Completeness, Pexels prohibition, DiagramSuitability
`NATIVE_MOTION_GRAPHIC`, EvidenceTruth `NOT_REQUIRED`, niche, market, semantic
spec fixture, continuity, repetitive-risk, rights và cost gates đều `PASS`.
Requirements hash `429f9ca4...`; decision hash `0e08a51c...`.

Run-state SHA-256 trước audit `76909161...`; provider calls `4 → 4`; render,
Drive, YouTube đều `0`. Chi tiết 20 candidates, exact hashes và gate matrix nằm
trong `reports/sc07_visual_route_audit_summary.json`.

# R3D9 Runtime Dashboard Ops / Backfill + Channel Runtime Trace

Ngày: 2026-07-04

## Kết luận

R3D9 đã được implement theo hướng read-model/operator cockpit. Không thêm engine mới, không chạy job mới, không execute provider, không upload/publish.

Prerequisite reports đã có trong `reports/`: R3D1-R3D8, M1, M2, DX1.

## Files changed

- `app/contracts/r3d9.py`
- `app/services/r3d9.py`
- `app/main.py`
- `frontend/src/lib/types.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/features/ops/ops-view.tsx`
- `frontend/src/features/ops/__tests__/ops-view.test.tsx`
- `tests/test_r3d9_runtime_dashboard_ops.py`
- `reports/r3d9_runtime_dashboard_ops_report.md`

Không thêm migration.

## Backend services/read models

Đã thêm các service/read-model R3D9:

- `RuntimeDashboardService`
- `ChannelRuntimeTraceService`
- `PackageOpsSummaryService`
- `UploadedVideoOpsService`
- `DiagnosticOpsService`
- `RecoveryOpsService`
- `LearningOpsService`
- `MemoryOpsReadModelService`
- `RetrievalOpsTraceService`
- `MemoryInfluenceOpsService`
- `QualityDeltaOpsService`
- `ProviderCostOpsService`
- `OperatorNextActionService`

Các model response chính nằm trong `app/contracts/r3d9.py`.

## API endpoints

Đã thêm GET-only ops endpoints:

- `GET /ops/command-center`
- `GET /ops/next-actions`
- `GET /channels/{channel_id}/runtime-trace`
- `GET /video-projects/{project_id}/runtime-trace`
- `GET /video-packages/{package_id}/ops-summary`
- `GET /uploaded-videos/{uploaded_video_id}/ops-summary`
- `GET /diagnostics/queue`
- `GET /recovery/queue`
- `GET /learning/queue`
- `GET /memory/review-queue/ops`
- `GET /retrieval-manifests/{manifest_id}`
- `GET /memory-influence/{manifest_id}`
- `GET /quality-delta/{quality_delta_id}`
- `GET /provider-cost/{package_id}`

Không thêm endpoint chạy daily/no-view/vector/provider/upload.

## Frontend

Đã mở rộng `OpsView` thành cockpit R3D9:

- Ops Command Center
- Channel Runtime Trace panel
- Package Ops Summary panel
- Uploaded Video Monitor panel
- Diagnostic / Recovery queue
- Learning Review queue
- Memory Approval queue
- Retrieval Manifest debug panel
- Memory Influence panel
- Quality Delta panel
- Provider / Cost Firewall panel

Raw UUID/hash/JSON nằm trong `TechnicalAppendix`/details. Retrieval manifest ẩn raw memory text mặc định.

## Allowed human actions

R3D9 chỉ expose next action/read-model cho operator:

- review package
- tạo/đọc manual upload handoff qua flow hiện có
- manual upload outside VCOS
- backfill `video_id/url` qua M12.2 flow hiện có
- verify uploaded video qua flow hiện có
- review recovery proposal nếu workflow hiện có hỗ trợ
- review learning candidate nếu workflow hiện có hỗ trợ
- review memory item theo R3D5 queue
- xem paid render approval/provider boundary theo R3D8

R3D9 không tự thực hiện các action này.

## Forbidden actions

Không có:

- daily generation button/endpoint
- NoView run button/endpoint
- vector learning run button/endpoint
- provider execution button/endpoint
- YouTube upload API
- browser/Studio scraping
- Drive upload
- auto publish/upload/reupload
- Channel Contract/ChannelProfileVersion mutation
- learning auto-promotion
- prompt self-mutation

## ChannelRuntimeTrace

`ChannelRuntimeTraceService` lấy runtime truth từ `EffectiveChannelRuntimeContextSnapshot` và snapshot refs:

- `channel_profile_version_id`
- `compiled_policy_snapshot_id`
- `channel_contract_hash`
- `effective_context_snapshot_id`
- `context_hash`
- category/character nullable
- market/locale/language
- voice profile
- thumbnail style
- publish timing policy
- provider/budget boundary
- source refs

Test đã mutate setting mutable của channel sau khi snapshot tạo; trace vẫn trả publish timing từ snapshot, không đọc latest mutable setting.

## Package/backfill/manual upload cockpit

`PackageOpsSummaryService` tổng hợp:

- package status/project/channel
- effective context snapshot/hash
- `AgentContextPackSnapshot` refs và prompt budget
- hook/first 3 seconds
- title/description/subtitle/disclosure
- thumbnail handoff
- publish timing recommendation
- R3D4 gate batch/run results
- Gatekeeper/packaging gate summary nếu có
- provider boundary summary
- manual publish handoff/checklist/manual-only warning
- next action

`UploadedVideoOpsService` đọc M12.2 ledger/backfill:

- platform video id/url
- backfill history
- verify status
- actual upload/publish time
- channel/operator timezone
- analytics sync/maturity/confidence
- diagnostic/recovery/learning refs

Không scrape YouTube Studio.

## Memory/retrieval/quality delta

Memory queue hiển thị approval/rights/prompt safety/freshness/scope/gate summary và `prompt_eligible=false` nếu thiếu `APPROVED + SAFE + PROMPT_SAFE + FRESH`.

Retrieval manifest hiển thị:

- effective context snapshot
- agent/use case
- SQL filter
- candidate count trước/sau policy
- selected/blocked/rejected refs
- retrieval/digest hash
- raw memory hidden by default

Memory influence hiển thị agent/package/retrieval manifest/facet refs/digest/prompt context hash/scope status.

Quality delta hiển thị facet used, expected metric/direction, baseline/observed snapshot, result, confidence delta, reason codes, next action.

## Provider/cost firewall

`ProviderCostOpsService` đọc R3D8 records:

- provider readiness
- missing config
- `RenderRevision`
- `CostEstimateSnapshot`
- `HumanPaidRenderApproval`
- `PaidAttemptLimitRecord`
- boundary decisions
- `PaidProviderCallLedger`
- proxy preview flags
- `will_execute=false`

Không có execute button. Nếu DX2 drift guard fail, panel không được coi READY.

## Tests run

Đã pass:

- `pytest tests/test_r3d9_runtime_dashboard_ops.py -q` → 2 passed
- `npm run test -- ops-view.test.tsx` → 1 passed
- `pytest tests/test_dx2_provider_stack_reconciliation.py -q` → 7 passed
- R3D1-R3D8 subset → 104 passed
- M1/M2 subset → 54 passed
- M12.2/M12.2S qualification subset → 43 passed
- `pytest tests/test_migration.py tests/test_dx1_semantic_code_convention.py -q` → 8 passed
- `.venv/bin/python -m compileall -q app` → passed
- `npm run typecheck && npm run lint && npm run test` → passed
- `git diff --check` → passed

## Proof no provider/media/upload execution

- R3D9 endpoints là GET-only.
- R3D9 service chỉ query/serialize existing runtime records.
- `ProviderCostOpsRead.will_execute=false`.
- Test scan `app/services/r3d9.py` không thấy token execute/upload/scrape/Studio.
- Frontend test assert không có button daily/NoView/vector/provider/render/upload/YouTube.

## R3D10 follow-up

- Freeze R3D9 API/read-model contract.
- Gate R3D10 start bằng DX2 `ProviderStackDriftGuard=PASS`.
- Chốt no-go checklist: no provider execution, no upload, no mutable runtime truth, no job-control UI.

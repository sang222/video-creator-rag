# Long Production Orchestrator

LPRO1 nối `FirstScriptedVideoPackage` đã qua human review với media draft có thể xem, không tạo pipeline song song. Authority đầu vào là package/project lineage đã đóng băng; API không nhận topic, script, profile, niche, timeline hay asset override.

Lifecycle chuẩn đi từ `PACKAGE_ACCEPTED` qua narration, verified alignment, `CanonicalMediaTimeline`, asset resolution, `NativeRenderPlan`, compiler, FFmpeg, technical QC và kết thúc tại `READY_FOR_HUMAN_REVIEW`. Trạng thái plan/package không bao giờ đồng nghĩa MP4 đã tồn tại. `READY_FOR_HUMAN_REVIEW` chỉ xuất hiện sau MP4 thật và TechnicalMediaQC PASS.

`OFFLINE_FIXTURE` dùng adapter deterministic local, tạo zero provider call và review candidate `production_eligible=false`, `not_publishable=true`. `REAL_APPROVED_PRODUCTION` yêu cầu `ProductionRenderExecutionEnvelope` exact-hash; LPRO1 vẫn fail-closed vì MR1 đang ON_HOLD.

`LongFormRenderPackageStrictContract` là projection nghiêm ngặt trong domain M10.2 hiện hữu. Legacy package vẫn đọc được nhưng không vào strict production. Canonical timeline là temporal authority duy nhất; estimated M6 timing chỉ là lịch sử.

FinalMediaRef không được tạo ở draft boundary. Closeout production yêu cầu candidate exact hash, human PASS, technical PASS, creative acceptance, archive PASS khi bắt buộc, package lineage, provenance/rights và file checksum đầy đủ.

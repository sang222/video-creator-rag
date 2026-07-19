# Channel Profile v3 Market Policy

v3 là overlay typed trên active v2. Compiler copy-forward toàn bộ v2 rồi chỉ thêm bảy block market; semantic diff từ chối mọi path ngoài allowlist.

## Blocks

- `TargetMarketProfile` và bounded `TargetMarketDigest` là production truth.
- `MarketAlignmentPolicy` khóa thứ tự preflight/topic/research/script/voice/visual/thumbnail/metadata.
- `DestinationBindingPolicy` tách account country, target market và geo người xem; publish cần VERIFIED destination.
- `MarketPackageFreezePolicy` yêu cầu QC, human review, market dossier, publish risk, destination và exact package approval.
- `PublishTimingLocalizationPolicy` khóa en-US, America/New_York, EN_US_MASTER_ONLY và manual publish.
- `GeoEvaluationPolicy` yêu cầu tối thiểu 3 video, cửa sổ 7/30 ngày, không đổi strategy từ một video và tách paid/organic.

Compiler phát hành hash-bound snapshot refs cho từng block. Project mới freeze exact profile/digest/market/locale/narration/timezone; historical project không latest-lookup và không bị rewrite.

Destination `PENDING_PLATFORM_ID` không chặn profile activation nhưng luôn chặn publish execution. Điều này cho phép chính sách đúng được active mà không bịa platform ID hoặc credential.

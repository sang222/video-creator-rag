# Destination và Market Package Freeze

Luồng chuẩn:

`Approved TargetMarketProfile → DestinationBinding → MarketAlignmentDossier → PublishRiskMarketAlignment → exact package approval → MARKET_PACKAGE_FROZEN → manual publish`

## Invariants

- `account_country`, `target_market`, `actual_viewer_geography` là ba trường độc lập.
- VERIFIED destination cần platform channel ID, credential reference và verification timestamp thật.
- Destination sai workspace, chưa verified hoặc không khớp market profile chặn freeze.
- Freeze chỉ PASS khi media QC, creative review, market dossier, publish risk và exact human approval đều PASS.
- Hash bỏ các trường receipt/state tự tham chiếu; mọi content mutation làm integrity BLOCK và yêu cầu version/hash/approval mới.
- `UPLOAD_READY` không tồn tại khi thiếu final media file/hash.
- Không có upload/publish API trong flow này.

Persistence tái sử dụng JSON metadata versioned của `ChannelWorkspace` và typed artifact payload; không cần migration mới.

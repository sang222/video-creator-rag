# GEO2 — Market-aware Channel Init & Package Freeze

## Kết quả

`GEO2_FINAL=PASS`. Backend và dashboard dùng cùng typed market contracts; không gọi provider, Drive hoặc YouTube.

## Implementation

- Channel Init nhận 8 trường cốt lõi, tách `account_country` khỏi target market.
- Setup/Research Agent chạy bằng offline fixture, tạo proposal có confidence, evidence, rationale và cờ human confirmation.
- Operator sửa và duyệt đúng `draft_id/version/hash`; draft không có quyền activate.
- `DestinationBinding` versioned, hash-bound; trạng thái VERIFIED bắt buộc platform ID, credential ref và timestamp thật.
- `PublishRiskMarketAlignment`, `MarketBoundPublishPackage` và `MARKET_PACKAGE_FROZEN` khóa destination, market profile, media, metadata, captions, disclosure và publish window.
- Integrity check buộc package version/hash/approval mới sau mọi thay đổi.
- Dashboard có 5 bước init, market profile/destination read model, Market Alignment panel và publish queue market-bound; không có auto-publish.

## Verification

- Backend GEO2 + packaging handoff: 16 passed.
- Frontend: 35 tests passed; typecheck, ESLint và Next production build PASS.
- Alembic: một head `0038_lpro1_daily_mode`.
- External execution: provider=0, Drive=0, YouTube=0.

## Boundary

Organic không có `target_country` per video. VCOS chỉ tạo market-native package, khóa đúng destination, publish thủ công và chờ đo geo thật sau publish.

`MR1_EXECUTION=ON_HOLD`
`PROCEED_TO_MR1=false`
`PROCEED_TO_CHANNEL_PROFILE_V3=true`

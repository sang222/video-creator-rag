# CH1-MARKET-v3 — Small Team AI

## Kết quả production

`CH1_MARKET_V3_FINAL=PASS`. `ChannelProfileVersion v3` và compiled snapshot v3 đã active cho `small-team-ai`.

| Binding | Giá trị |
|---|---|
| Active profile | v3 · `d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711` |
| Active snapshot | `e6c33d80-f5d8-4f72-9abc-87de3601b89e` |
| Target market | US |
| Locale / narration | en-US / en-US |
| Timezone / currency | America/New_York / USD |
| Visual profile | STOCK_ASSISTED |
| Destination | YouTube `@SmallTeamAI` · PENDING_PLATFORM_ID |
| Publish execution | BLOCKED cho tới khi destination được verify bằng platform ID/credential thật |
| Rollback | profile v2 / snapshot `6304e2a4-f096-410b-af09-a2748b311855` |

`account_country` không có evidence trong repository nên giữ `null`; không suy đoán. Destination không bị gắn nhãn VERIFIED giả.

## Exact v2 → v3 diff

- Modified: policy version và exact operator approval ref.
- Added: TargetMarketProfile, TargetMarketDigest, market gate policy, destination policy, market package freeze, localization/publish timing và geo evaluation foundation.
- Removed: none.
- Tất cả editorial, visual, provider, budget, evidence, archive policy v2 giữ nguyên.

Visual invariant vẫn là Gemini Image `gemini-3.1-flash-image`, 2K, một output/một attempt, không fallback; Pexels chỉ supporting; exact text/number native-only; generated evidence=false; human visual approval và archive verification bắt buộc.

## Verification

- Required backend + D2P/LPRO regression: 39 tests PASS.
- Exact CH1 prompt suite rerun: 23 tests PASS.
- M11/dashboard: 6 tests PASS.
- Frontend: 35 tests, typecheck, lint, production build PASS.
- Alembic: một head `0038_lpro1_daily_mode`.
- Production execution deltas: provider/media/Drive/upload/YouTube đều 0.

`MR1_EXECUTION=ON_HOLD`
`PROCEED_TO_MR1=false`
`PROCEED_TO_PKG1_REVISION=true`

# Geo/Market Delivery Closeout

Kết quả: **PASS** ở mức implementation closeout. Destination production vẫn là
`PENDING_PLATFORM_ID`; vì vậy `UPLOAD_READY=false` và
`PUBLISH_EXECUTION_READY=false` được giữ nguyên.

| Binding | Giá trị |
|---|---|
| Dedicated project | `4624b948-897e-4112-b680-752beb44a7da` / `GEO_MARKET_DELIVERY_CLOSEOUT` |
| Active profile v3 | `d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711` |
| Base snapshot v3 | `e6c33d80-f5d8-4f72-9abc-87de3601b89e` / `12b66551bd9bdfce1d59d1019ff50bc1c49756b6dc4ab505fde080630b4551bc` |
| Base monetization truth | `primary=mixed`, channels=`adsense, affiliate` (không sửa) |
| Effective ads-only overlay | `72c42303-cdeb-46c4-9b90-881f2f7fd14e` / `d2595b424ab27a5ba84b33aad171251261225e3673531c415c8a55c4a50ea9db` |
| Effective market policy hash | `a0e37064715370a137ddc142a4d844076dc0c0670db4bfb67cd25d9727218b85` |
| Closeout evidence | `312474a4-adc1-4979-aec4-a20376e91e0c` / `8650d6ec33fe82848aefc6a4814dbb8e8560e70a297280f94d347d676ee8b178` |
| Destination binding | `1ea640c1-2330-56d3-97bc-ecb591c7b19d` / `411aae66418315da8e6a0bf2cd23e896e89e7cd4827a5b54c36c0437ad63efab` |
| Destination status | `PENDING_PLATFORM_ID` |

Overlay chỉ tạo effective policy `PLATFORM_AD_REVENUE_ONLY` trên exact base hash;
không mutate `ChannelProfileVersion v3` hay `CompiledChannelPolicySnapshot v3`,
không giả lập channel ID/verification, và không cấp provider/render/publish authority.

## Acceptance

| Gate | Verdict |
|---|---|
| `ADS_ONLY_MONETIZATION_POLICY` | `PASS` |
| `GEO_DELIVERY_CLOSEOUT_DESTINATION_ENFORCEMENT` | `PASS` |
| `GEO_DELIVERY_CLOSEOUT_MARKET_ALIGNMENT` | `PASS` |
| `GEO_DELIVERY_CLOSEOUT_MARKET_LINEAGE` | `PASS` |
| `GEO_DIAGNOSTIC_RULES` | `PASS` |
| `GEO_DISTRIBUTION_TRACKER` | `PASS` |
| `GEO_MATURITY_INTEGRATION` | `PASS` |

## Machine verification manifest

- Manifest hash: `a881d30bf8189b69376b3b9c012fbcdaaa5f7bdf3d257d979e99b439b201c691`
- Producer: `VCOS_MACHINE_VERIFICATION_RUNNER`
- Relevant-workspace hash: `c97c08f79c784c04745dcbe75efb82cda9a0f4d6c7bb421505673df9cae69fd4`
- Repository revision authority: `workspace-sha256:c97c08f79c784c04745dcbe75efb82cda9a0f4d6c7bb421505673df9cae69fd4`

| Run | Command | Exit | Passed | Failed | Skipped | Output hash |
|---|---|---:|---:|---:|---:|---|
| `geo-delivery-focused-and-regression` | `/Users/sangss/Desktop/video-creator-rag/.venv/bin/python -m pytest -q tests/test_geo_market_delivery_closeout.py tests/qualification/test_m7_publish_handoff.py tests/qualification/test_m9_post_publish_diagnostics.py tests/qualification/test_m12_2r_publish_handoff_ledger.py` | `0` | `42` | `0` | `14` | `1e94279afbb810e176d7559d92f84f9f5eb6608bfb80b119420edfd41ba3407c` |
| `geo-delivery-compileall` | `/Users/sangss/Desktop/video-creator-rag/.venv/bin/python -m compileall -q app scripts` | `0` | `1` | `0` | `0` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `geo-delivery-alembic-head` | `/Users/sangss/Desktop/video-creator-rag/.venv/bin/alembic heads` | `0` | `1` | `0` | `0` | `8888ff68099f86ffbb6ec3099a4b5d336fc38d864681ae447470e98a2c073bb6` |
| `geo-delivery-git-diff-check` | `git diff --check` | `0` | `1` | `0` | `0` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

# PEXELS-DL1 — Real Pexels Media Download Boundary Repair

Ngày: 2026-07-13
Phạm vi: offline architecture repair; không chạy PA1R và không gọi provider.

## Original failure classification

Evidence được đọc read-only từ `pa1r-20260712-guarded-smoke-002`; ledger/failure evidence cũ không bị sửa.

| Evidence | Giá trị an toàn |
|---|---|
| provider asset ID | `32150707` |
| provider file ID | `13707650` |
| selected rendition | MP4, 1920x1080, 9 giây |
| URL scheme tại downloader | `UNKNOWN_NOT_CAPTURED` |
| initial/final media host | `UNKNOWN_NOT_CAPTURED` |
| query present | `UNKNOWN_NOT_CAPTURED` |
| explicit request header names | `[]` theo pre-repair call site; không được durable evidence capture |
| redirect count | `UNKNOWN_NOT_CAPTURED` |
| final HTTP status | `403` |
| final content type | `UNKNOWN_NOT_CAPTURED` |
| exception class | `HTTPError` |
| `.part` state sau failure | absent; source directory rỗng |
| final size/checksum | không tồn tại |

Confirmed reason code:

```text
MEDIA_HTTP_FORBIDDEN
```

Pre-repair code chuyển trực tiếp selected `video_files[].link` trong memory; durable plan chỉ giữ redacted reference. Không có evidence cho page/image/preview URL selection, credential leak hoặc `volatile://` execution. Tuy nhiên boundary cũ không capture raw-link scheme/host/query boolean, redirect lifecycle hay response content type, nên nguyên nhân provider/CDN cụ thể của HTTP 403 vẫn `UNDETERMINED_FROM_AVAILABLE_EVIDENCE`.

Root cause có thể khẳng định ở phía VCOS: API và media dùng chung generic transport, thiếu explicit transient context, redirect/SSRF policy, response validation, media probe và safe error evidence. PEXELS-DL1 sửa architectural root cause này; không tuyên bố đã real-verify provider behavior.

## Repair

- `PexelsRenditionSelector` chỉ nhận `video_files[].link` có file ID, HTTPS, `video/mp4`, dimensions và orientation hợp lệ; HLS/`.m3u8` bị loại.
- `PexelsDownloadPlan` durable giữ `volatile_download_reference`, `download_url_hash`, `expected_media_host`, `query_present`; không giữ raw URL/query values.
- `PexelsDownloadExecutionContext` chỉ được tạo từ selected API rendition, dùng filename từ sanitized asset/file IDs, không serialize/pickle/repr raw URL và expire sau attempt.
- Context raw URL hash/host/query/IDs/MIME/dimensions phải khớp durable plan; `volatile://` không executable.
- `PexelsMediaDownloadClient` tách khỏi API client; chỉ gửi `Accept` và `User-Agent`. API key, Authorization, X-API-Key và Cookie không đi vào media boundary.
- URL được truyền nguyên chuỗi tới transport; redirect được follow thủ công để kiểm tra HTTPS, public DNS/IP và giới hạn hop trước mỗi request.
- Chỉ nhận HTTP 200/206, `video/mp4` hoặc allowed `video/*`, body không rỗng và dưới byte cap.
- Stream vào `.mp4.part`, SHA-256 trong lúc đọc, fsync, ffprobe/shape validation, sau đó atomic rename. Failure xóa `.part`; existing final file không bị overwrite.
- Safe evidence chỉ chứa host, query boolean, redirect/status/content metadata, byte count, exception/reason, provider IDs và cleanup result.

## Code paths changed

- `app/contracts/asset_acquisition.py`
- `app/services/provider_asset_manifests.py`
- `app/services/pexels_media_downloader.py`
- `app/services/pa1r.py`
- `tools/pa1r/run_pa1r.py`
- `tests/test_as1_asset_acquisition_provenance.py`
- `tests/test_pa1r_guarded_provider_smoke.py`
- `tests/test_pexels_real_download_boundary.py`
- `docs/operations/pa1r_guarded_provider_smoke.md`

## Fixture and no-execution proof

Fixture flow:

```text
local API payload
→ selected signed video_files[].link
→ httpx.MockTransport 302
→ httpx.MockTransport 200 video/mp4
→ streamed .part
→ SHA-256 + ffprobe
→ atomic final MP4
```

Covered: exact signed query preservation, durable/transient separation, non-executable volatile reference, redirects, API/media header isolation, HTML/empty/HLS rejection, `.part` cleanup, checksum/atomic success, duplicate protection, HTTPS/private-target SSRF rejection, raw URL absence from durable/error evidence.

Tests monkeypatch DNS và dùng only `httpx.MockTransport`; no real provider/network request occurred. PA1R CLI không được chạy. Old run attempt ledger không reset. Focused required suite: 67 PASS; compileall PASS; `git diff --check` PASS.

## New-run requirement

Repair chỉ đủ điều kiện cho một PA1R run mới. Không reuse `-001`/`-002`. Run kế tiếp cần explicit operator approval, run ID mới, idempotency keys mới, planned ledger mới và đúng một Pexels search/download attempt.

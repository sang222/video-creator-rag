# Long Production Execution

Trigger kiểm soát:

- `POST /daily-idea-decisions/{decision_id}/production-handoff/run` tạo/resume D2P1, không render.
- `POST /video-projects/{project_id}/long-production/run` chỉ nhận package identity, execution mode và envelope.
- `GET /video-projects/{project_id}/long-production` chỉ đọc trạng thái durable.

Qualification dùng `OFFLINE_FIXTURE`. Nó tạo WAV local, alignment deterministic, timeline/caption canonical, ba asset local (native diagram, stock-like motion, generated-like still có native overlay), normalize bằng FFmpeg, compile và render H.264/AAC 1920×1080@30. NativeMediaQC giải mã toàn file, ffprobe stream, đo drift, black output, caption lower band và scene fingerprints.

Không chạy `REAL_APPROVED_PRODUCTION` khi chưa có MR1 scoped approval. Envelope hợp lệ chỉ chứng minh executor eligible; cờ production execution vẫn chặn lệnh FFmpeg thật trong LPRO1. Không gọi Drive/YouTube.

Khi retry cùng frozen lineage, dùng receipt/run key cũ và không render lại. Nếu lineage hash đổi, tạo run/version mới. MP4 fixture nằm dưới `artifacts/lpro1/runs/<run-id>/` và luôn là non-production review candidate.

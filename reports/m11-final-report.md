# M11 Final Report - Operator Dashboard / VCOS

## Verdict
M11 Operator Dashboard foundation đã được triển khai ở backend và frontend. M11 mở dashboard đọc/tổng hợp, quyết định lifecycle do người vận hành, learning review decision, và approved playbook entries. Không commit, không tag.

## Repo path
`/Users/sangss/Desktop/video-creator-rag`

## Preflight status
- Working tree ban đầu sạch trên `main`.
- Tag bắt buộc `m10-5-google-drive-media-offload` tồn tại.
- Đã đọc README, source-of-truth, architecture ledger, và reports M10, M10.1, M10.2, M10.3, M10.4, M10.5 trước khi mở M11.
- Backend regression trước M11: `.venv/bin/pytest -q` -> `215 passed, 4 skipped, 1 warning`.

## Backend status
- Alembic head mới: `0016_m11_operator_dashboard`.
- Thêm tables: `channel_lifecycle_decisions`, `learning_review_decisions`, `approved_playbook_entries`.
- Thêm models, contracts, services, route handlers, audit/domain events, và guard quyền thao tác M11.
- `ChannelWorkspace` chấp nhận thêm trạng thái `ready` và `deactivated` cho lifecycle dashboard.

## Frontend status
- Thêm Next.js App Router app tại `frontend/`.
- App tên vận hành: Signal Deck.
- UI là cockpit trực tiếp, không landing page marketing.
- Build có Command Center, channels, channel init wizard, channel workspace, queues, publishing, uploaded videos, learning, media, ops, projects, settings.

## Test status
- Pre-M11 backend full suite: `215 passed, 4 skipped, 1 warning`.
- M11 targeted backend qualification: `14 passed, 1 warning`.
- Frontend: `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build`, `npm run e2e` đều pass.
- Frontend production audit: `npm audit --omit=dev` -> `found 0 vulnerabilities`.
- Config seed: `PYTHONPATH=. .venv/bin/vcos config seed` -> `config seed ok: 146 catalogs`.
- Post-fix failing regression subset: `16 passed, 1 warning`.
- Final post-M11 full backend regression: `.venv/bin/pytest -q` -> `219 passed, 4 skipped, 1 warning`.

## Implemented scope
- M11 dashboard read models and API endpoints.
- Human lifecycle decisions for channels.
- Human learning review decisions and approved playbook promotion records.
- Frontend operator dashboard foundation.
- Docs and qualification guards updated to make M11 the current boundary.

## Frontend stack
Next.js 15, React 19, TypeScript, Tailwind, Radix UI, TanStack Query, TanStack Table, React Hook Form, Zod, Recharts, Zustand, Vitest, Testing Library, Playwright.

## App routes/pages added
- `/`
- `/channels`
- `/channels/new`
- `/channels/[channelId]`
- `/queues`
- `/queues/[queueType]`
- `/publishing`
- `/uploaded-videos`
- `/uploaded-videos/[uploadedVideoId]`
- `/learning`
- `/media`
- `/ops`
- `/projects`
- `/settings`

## Backend/API added
- `GET /dashboard/command-center`
- `GET /dashboard/queues`
- `GET /dashboard/queues/{queue_type}`
- `GET /providers/status`
- `GET /ops/health`
- `GET /channels`
- `GET /channels/{channel_id}/workspace`
- `GET /channels/{channel_id}/lifecycle`
- `POST /channels/{channel_id}/lifecycle-decision`
- `GET /uploaded-videos`
- `GET /uploaded-videos/{uploaded_video_id}/dashboard`
- `POST /learning-candidates/{candidate_id}/approve`
- `POST /learning-candidates/{candidate_id}/reject`
- `POST /learning-candidates/{candidate_id}/request-more-evidence`
- `POST /learning-candidates/{candidate_id}/suppress`
- `POST /learning-candidates/{candidate_id}/expire`

## Command Center
Command Center gom action cards, metrics, queues, provider/ops status, và safety warnings. Read path không gọi provider thật, không scrape, không upload/publish/reupload.

## Channel Init/Profile
Frontend channel init wizard dùng existing backend flow: create channel, create profile version, compile policy snapshot, activate. M11 chỉ thêm metadata dashboard-safe; không tự động sinh config upgrade.

## Channel Workspace
Channel workspace hiển thị lifecycle, policy/profile refs, project summary, publishing handoff, uploaded video, learning, media, diagnostics, và ops snippets từ DB truth hiện có.

## Approval queues
Queues gom review tasks, manual action queue records, publish handoff packages, learning review queue items, recovery proposals, và provider incident work. Queue routes là read model, không tự chạy action nguy hiểm.

## Publish handoff
M11 chỉ hiển thị handoff/manual confirmation/uploaded video trạng thái. Không thêm YouTube upload API, scheduled upload, publish-now, auto publish, hoặc platform edit.

## Uploaded Videos / YouTube follow
Uploaded video dashboard đọc M7/M8/M10.3 payloads: publication summary, analytics summary, public monitor, owner analytics, observation windows. M11 không sync YouTube mới và không scrape YouTube Studio.

## Google Drive media CTA
Media card dùng Google Drive `web_view_link` làm CTA duy nhất. Dashboard không expose local path, backend download URL, preview proxy URL, hoặc Drive streaming qua VCOS.

## Diagnostics / Recovery
M11 hiển thị M9 diagnostics và recovery proposals để người vận hành review. Không tự áp dụng recovery, không tự đổi title/thumbnail/metadata.

## Learning Review / Playbook Promotion
M11 thêm human actions approve, reject, request-more-evidence, suppress, expire. Approve tạo `approved_playbook_entries` có evidence refs và audit/domain events. Không mutate `ChannelProfileVersion`, `CompiledPolicySnapshot`, workflow, config, title, thumbnail, hoặc platform metadata.

## Derivative / Shorts
M11 chỉ hiển thị derivative/shorts/funnel state đã có từ M10.1. Không thêm auto clipping, auto publish, hay platform loop.

## Provider / Ops
Provider/Ops dashboard đọc registry, credential health, quota/cost/health snapshots, incidents, attempts, và dead-letter jobs. Không gọi real provider trong read path.

## RBAC/permissions
Role catalog M11 có 13 role. Mutating dashboard actions bị chặn với read-only observer. Learning review cần owner/admin hoặc learning reviewer. Channel lifecycle cần owner/admin hoặc channel manager.

## Safety constraints
- No auto upload/publish/reupload.
- No fake traffic, bot engagement, IP/VPS tricks, hoặc platform evasion.
- No local media path exposure.
- No backend media download/preview proxy.
- No raw credential/OAuth token exposure.
- No automatic profile/policy/config mutation from learning.

## Scope explicitly not built
- Real upload/publish APIs.
- Dashboard-driven provider execution.
- Vector/RAG engine.
- OPA/Cedar.
- Browser automation or scraping.
- TikTok/Facebook analytics loops.
- Auto recovery application.
- Real config editor/config upgrade workflow.
- Production-grade generated OpenAPI frontend client.

## Risks / limitations
- Frontend API client hiện hand-typed; OpenAPI generation nên là polish step.
- RBAC hiện là service-level role guard for M11 actions, chưa phải full policy engine.
- UI dùng dashboard read models nền tảng; deep edit flows vẫn thuộc milestone sau.
- Dev audit toàn bộ dependency có thể còn warning ở dev tooling; production audit đã sạch với `npm audit --omit=dev`.

## Next suggested milestone
M12 nên tập trung vào operator edit workflows an toàn: channel config draft/review/publish, audit-rich config diff, OpenAPI-generated frontend client, và UI state hardening cho production operator use.

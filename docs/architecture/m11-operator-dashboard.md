# M11 Operator Dashboard

M11 adds the VCOS Operator Dashboard / Signal Deck cockpit.

## Scope

- Action-first Command Center read model.
- Unified approval queues for learning, publish handoff, recovery, and ops manual action.
- Channel workspace and lifecycle read/decision backend.
- Learning review decisions and approved playbook entries.
- Uploaded video dashboard read model with YouTube public/owner analytics sections.
- Google Drive media cards using `CloudMediaRef.web_view_link` only.
- Provider / cost / ops health read model.
- Next.js App Router frontend in `frontend/`.

## Backend Truth

New runtime tables:

- `channel_lifecycle_decisions`
- `learning_review_decisions`
- `approved_playbook_entries`

Channel lifecycle decisions are human decisions. Health status observes and warns, but does not auto-pause, deactivate, archive, or reactivate a channel.

Approved playbook entries preserve evidence refs, limitations, counter-evidence, and policy/rights summaries. Approval does not mutate `ChannelProfileVersion`, `CompiledChannelPolicySnapshot`, daily workflow, production workflow, or platform metadata.

## Dashboard Rules

- No auto publish/upload/reupload.
- No YouTube upload API.
- No dashboard scraping or browser automation.
- No fake traffic, bot engagement, platform evasion, IP/VPS tricks, or reupload spam.
- No automatic config upgrade suggestion.
- No automatic profile/policy mutation from learning.
- No backend Drive download endpoint.
- No backend Drive preview/proxy endpoint.
- Media UI uses Google Drive `web_view_link` CTA only.

## Frontend

`frontend/` uses:

- Next.js App Router
- React strict TypeScript
- Tailwind CSS
- shadcn-style local components with Radix primitives
- TanStack Query
- TanStack Table
- React Hook Form + Zod
- Recharts
- Vitest + Testing Library
- Playwright smoke

Primary routes:

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

## Limitations

- Channel Init uses existing channel/profile/compile/activate APIs and stores M11 config in channel metadata for this foundation pass.
- Artifact diff, gate resolution, recovery decision mutation, and manual publish confirmation UI are represented in queue/read paths but can be hardened in an M11 repair pass.
- Frontend API client is hand-typed against M11 dashboard contracts; OpenAPI generation remains a polish task.

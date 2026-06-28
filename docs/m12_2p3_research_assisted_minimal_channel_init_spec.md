# M12.2P3 Research-Assisted Minimal Channel Init Spec

Source of truth: Deep Research PDF `Đặc tả production-safe cho Minimal Channel Init với Research Agent trong VCOS.pdf`, dated 2026-06-28.

## Verdict

Default Channel Init must move from template-first heavy setup to minimal research-assisted setup, while keeping the existing full form as Advanced manual mode.

Flow:

MinimalAdminInput -> ChannelInitDraft -> ChannelSetupResearchAgent -> ChannelContractDraft -> human review/edit/confirm -> compile -> activate.

## Hard Rules

- Default flow does not use template presets.
- Research creates draft suggestions only.
- Research cannot mutate runtime truth.
- Research cannot activate channels.
- Research cannot create ChannelProfileVersion directly.
- Human review is required before COMPLETE.
- PARTIAL snapshots can exist for preview only and cannot activate.
- ChannelProfileVersion and CompiledChannelPolicySnapshot are created only by compile.
- Existing channels and historical VideoProject snapshot bindings remain valid.
- VCOS does not publish, upload, or reupload.
- YouTube Studio scraping and logged-in dashboard/browser automation are forbidden.
- Provider budgets are configured in Settings / Integrations, not per channel.
- Runtime prompts bind only frozen ChannelProfileVersion and CompiledChannelPolicySnapshot artifacts, never draft JSON.

## Minimal Admin Input

Required fields:

- company_id
- channel_name
- public_presence_mode: EXISTING_PUBLIC_CHANNEL or NEW_CHANNEL_NO_PUBLIC_FOOTPRINT
- operator_note_purpose
- owner_operator_language, default vi-VN
- source_usage_attestation

Required for EXISTING_PUBLIC_CHANNEL:

- youtube_url_or_handle, website_url, or at least one public social/profile link.

Optional hints:

- youtube_url_or_handle
- website_url
- social_profile_links
- intended_content_language
- intended_primary_market
- initial_topic_pillar_hints

If `public_presence_mode` is `NEW_CHANNEL_NO_PUBLIC_FOOTPRINT`, research may still run but should usually produce PARTIAL and human questions.

## Research Agent

ChannelSetupResearchAgent may use only:

- admin-provided YouTube URL/handle
- public YouTube channel metadata from allowed public API/page
- admin-provided website
- admin-provided public social/profile links
- admin-provided notes/docs
- optional public web snippets only when explicitly enabled

Forbidden:

- YouTube Studio scraping
- dashboard scraping
- logged-in browser automation
- private analytics
- invented audience behavior
- invented market demand
- invented rights/license evidence
- fake local relevance
- country manipulation
- copying unrelated channel config without evidence
- config mutation
- activation
- publish/upload

Adapters:

- YouTubePublicMetadataAdapter
- WebsiteMetadataAdapter
- PublicProfileAdapter
- AdminNotesAdapter
- OptionalWebSnippetAdapter, disabled unless connector is explicitly enabled

Evidence priority:

admin direct input -> first-party public channel source -> owner-controlled website -> owner-controlled social/profile -> external public snippets.

Confidence:

- HIGH: direct first-party evidence, or multiple consistent owner-controlled sources.
- MEDIUM: partial direct evidence plus bounded inference.
- LOW: weak signal, sparse source, or pattern inference.
- UNKNOWN: insufficient eligible evidence.

## Provenance

`channel_contract_json` stays canonical plain values.

`field_source_map_json` stores parallel provenance for every leaf path:

- value
- source_type
- confidence_label
- evidence_refs
- review_required
- editable_by_human
- locked_reason

## Human Review Boundary

Human actions:

- confirm field
- edit field
- reject field
- mark unknown
- add note

When a field is confirmed or edited, its source type becomes HUMAN_CONFIRMED and the decision is appended to `review_decision_log_json`.

Strategic fields require ADMIN_INPUT or HUMAN_CONFIRMED before COMPLETE:

- target market
- audience
- content language
- niche
- positioning
- content pillars
- format policy
- rights/disclosure policy unless global locked
- learning policy
- claim style
- market/locale

Research inference alone is not enough for these fields.

## Compiler

Merge precedence:

1. Global hard safety policy
2. Global provider policy
3. Human-confirmed values
4. Admin direct input
5. Admin hints
6. Public research evidence
7. Research inference
8. Compiler-derived fallback for safe non-strategic fields only
9. UNKNOWN

Hard safety cannot be overridden:

- auto_publish_allowed=false
- studio_scraping_allowed=false
- dashboard_scraping_allowed=false
- config_mutation_by_agent_allowed=false
- auto_promote_learning=false
- fake_traffic forbidden
- bot_engagement forbidden
- platform_evasion forbidden
- upload/publish remains human handoff

COMPLETE requires:

- required strategic fields are ADMIN_INPUT or HUMAN_CONFIRMED
- locked policy fields are present
- low-confidence research-only fields were reviewed
- market/locale explicitly selected or confirmed
- content language explicitly selected or confirmed
- audience persona explicitly selected or confirmed
- content pillars explicitly selected or confirmed
- rights/disclosure policy from locked policy or human confirmation
- format policy selected or confirmed

Compile may produce COMPLETE, PARTIAL, MISSING, STALE, or CONTRADICTORY.

## YouTube Country Rule

YouTube channel country/location must not become target market automatically. Treat it as weak auxiliary evidence only. If only country metadata exists, market remains UNKNOWN and requires human confirmation.

## API

Required endpoints:

- POST `/channel-init-drafts`
- GET `/channel-init-drafts/{draft_id}`
- POST `/channel-init-drafts/{draft_id}/research`
- POST `/channel-init-drafts/{draft_id}/review`
- POST `/channel-init-drafts/{draft_id}/compile`
- GET `/channel-init-drafts/{draft_id}/contract-preview`
- POST `/channels/{channel_id}/activate`

## Frontend

Minimal-first wizard is default.

Advanced manual mode keeps the existing heavy full form.

Required Vietnamese copy:

- `Dữ liệu khởi tạo kênh là nguồn sự thật vận hành sau khi người vận hành xác nhận.`
- `Kết quả research chỉ là đề xuất, chưa phải cấu hình runtime.`
- `VCOS không tự publish/upload/reupload.`
- `Không dùng YouTube Studio scraping.`
- `Ngân sách provider được cấu hình trong Cài đặt / Tích hợp, không nhập theo từng kênh.`

## Small Team AI Rule

For `https://www.youtube.com/@SmallTeamAI` and `https://smallteamai.com/`, research may suggest practical AI workflows, automation systems, and operating dashboards with evidence. Content language may be suggested as English but must remain review-required. Primary market must not be auto-set from YouTube country/location.

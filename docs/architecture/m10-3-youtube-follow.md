# M10.3 YouTube Follow Patch

M10.3 connects M7 `UploadedVideo` records to YouTube follow data for future M11 dashboard use. The human operator still uploads manually outside VCOS, then pastes the actual YouTube URL/video id through the existing M7 confirmation flow. M10.3 follows that canonical `UploadedVideo`; it does not publish, upload, edit, schedule, or reupload anything.

## Scope

- PublicMonitorMode uses the YouTube Data API with `YOUTUBE_DATA_API_KEY`.
- OwnerAnalyticsMode uses OAuth 2.0 credentials for YouTube Analytics API reads.
- Public monitor sync records views, likes, comments, minimal publish consistency fields, freshness, availability, and WEAK authority.
- Owner analytics sync records impressions, impression CTR, average view duration, average view percentage, estimated minutes watched, subscribers gained/lost, freshness, availability, and STRONG authority.
- M8 analytics truth is updated through `AnalyticsSyncRun`, `AnalyticsSnapshot`, `MetricAvailabilitySnapshot`, and `UploadedVideoMetricsSummary`.
- Dashboard-ready follow summaries are exposed through API and CLI read paths.

## Non-Scope

M10.3 does not build dashboard UI, OAuth connect/disconnect UI, YouTube upload/publish API, YouTube Studio scraping, browser automation, TikTok/Facebook analytics learning loops, ElevenLabs/Creatomate/Veo generation, channel config mutation, config upgrade suggestions, approved learning promotion, auto publish/upload/reupload, source scraping, vector/RAG, OPA/Cedar, fake traffic, bot engagement, or platform evasion.

## Config

Public monitor:

- `YOUTUBE_PUBLIC_MONITOR_ENABLED`
- `YOUTUBE_DATA_API_KEY`

Owner analytics:

- `YOUTUBE_OWNER_ANALYTICS_ENABLED`
- `YOUTUBE_OAUTH_CLIENT_SECRETS_FILE`
- `YOUTUBE_OAUTH_CLIENT_ID`
- `YOUTUBE_OAUTH_CLIENT_SECRET`
- `YOUTUBE_OAUTH_REDIRECT_URI`
- `YOUTUBE_OAUTH_SCOPES`

Local `.env` and OAuth client JSON files are not source truth and must not be committed.

## OAuth Flow

`GET /auth/youtube/start` creates a `youtube_oauth_sessions` row with a hashed state token, required scopes, redirect URI, and `STARTED` status, then redirects to Google consent with offline access requested.

`GET /auth/youtube/callback` validates the state, exchanges the authorization code, stores tokens in local ignored dev storage when no external secret store is available, updates `CredentialReference`, and marks the monitoring credential connected or failed. Raw authorization codes, access tokens, refresh tokens, API keys, and client secrets are not stored in plain DB fields.

`CredentialReference.secret_ref` stores handles such as `env://YOUTUBE_DATA_API_KEY` or `local_file://var/credentials/youtube/...`. Token metadata in DB is safe metadata only.

## Metric Truth

Public monitor fields:

- `viewCount` -> `views`
- `likeCount` -> `likes`
- `commentCount` -> `comments`
- title, published time, channel id/title, thumbnail, duration, definition, caption status, privacy status, public stats viewability

Owner analytics fields:

- `views`, `likes`, `comments`
- `impressions`
- `impressionClickThroughRate` -> `impression_click_through_rate`
- `averageViewDuration` -> `average_view_duration_seconds`
- `averageViewPercentage` -> `average_view_percentage`
- `estimatedMinutesWatched` -> `estimated_minutes_watched`
- `subscribersGained` / `subscribersLost`

Zero is a valid numeric value. Missing values are `UNKNOWN`. Platform-unsupported values are `NOT_AVAILABLE`. Public monitor data has WEAK learning authority; owner analytics has STRONG learning authority. YouTube remains the only learning authority in this mode.

## Dashboard-Ready Read Model

`UploadedVideoYouTubeFollowReadService` computes the M11-ready payload:

- public stats, source, last sync, freshness, and WEAK authority
- title, thumbnail, publish time, caption, visibility, title match, duration match
- owner connection state, owner metrics, last sync, freshness, and STRONG authority when connected
- unknown/unavailable metric lists
- next action text for operator workflow

This is payload preparation only. M11 owns the actual dashboard/operator UI.

## Durable Runtime Tables

M10.3 adds:

- `youtube_monitoring_credentials`
- `youtube_oauth_sessions`
- `youtube_public_sync_runs`
- `youtube_owner_analytics_sync_runs`
- `uploaded_video_youtube_public_monitor_snapshots`
- `uploaded_video_youtube_owner_analytics_snapshots`

The follow read model is service-computed rather than a separate table.

## Deferred

- M10.4 Google Vertex Veo AI Hero Provider Binding and config externalization audit is complete in current repo state.
- M11: dashboard/operator cockpit, Uploaded Video screen, OAuth connect/disconnect UI, approval UX/actions, channel config editing, and learning promotion UX.

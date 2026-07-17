# HPR1 Provider Stack Freeze

Google Veo is the sole external AI video hero provider. NativeFFmpegRenderer remains the only final assembly/render authority.

## Canonical external providers

- `elevenlabs`: narration/TTS only.
- `pexels_api`: supporting stock only; never the explanatory backbone or factual evidence.
- `google_veo`: selective AI hero/metaphor clips through `GEMINI_API_NATIVE`.

Local capability: `native_ffmpeg_renderer`. Archive infrastructure: `google_drive_archive`. YouTube remains manual publish plus read-only verification/analytics.

The system has no secondary external AI video provider and no provider failover chain:

```text
external_ai_video_provider_order=[google_veo]
AI_HERO_UNAVAILABLE
  -> NATIVE_VISUAL_REQUIRED when the frozen project policy permits
  -> otherwise REVIEW_REQUIRED or BLOCK
```

The native visual downgrade is an explicit source-role decision with provenance and cost-avoided evidence; it is not provider fallback.

## Veo 3.1 baseline

- approved models: `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview`;
- first PA1R default: `veo-3.1-fast-generate-preview`, 8 seconds, 720p, 16:9, one output;
- versioned pricing: `config/google_veo_model_price_catalog.yaml`;
- provider audio may exist, but current channel policy is `DISCARD`;
- ElevenLabs owns narration and NativeFFmpeg owns the final mix.

`VCOS_VEO_REAL_GENERATION_ENABLED=false` and `VCOS_PA1R_VEO_SMOKE_ENABLED=false` are the defaults. Provider and dashboard action endpoints are absent.

## Immutable/runtime boundary

Provider execution cannot mutate Channel Contract, ChannelProfileVersion, EffectiveChannelRuntimeContextSnapshot or FormatIdentityContract. Drive is archive-only and never a renderer or publish trigger. NativeFFmpeg never enters `PaidProviderCallLedger`.

CH1-FLEX compiles provider availability separately from channel use. `small-team-ai` permits Pexels only as optional supporting context and Google Veo only as an optional hero role, both with zero minimum quota. ElevenLabs is the narration authority; one controlled retry needs a new approval. The channel-scoped provider policy is frozen per project and does not add provider controls to the dashboard.

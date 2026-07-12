# Google Veo Activation Guard

HPR1 installs foundation only. It does not authorize or execute a provider call.

Required settings:

```text
VCOS_AI_VIDEO_HERO_PROVIDER=google_veo
GEMINI_API_KEY=<secret>
VEO_MODEL_ID=veo-3.1-fast-generate-preview
VEO_DEFAULT_DURATION_SECONDS=8
VEO_DEFAULT_RESOLUTION=720p
VEO_DEFAULT_ASPECT_RATIO=16:9
VEO_DEFAULT_OUTPUT_COUNT=1
VCOS_VEO_REAL_GENERATION_ENABLED=false
VCOS_PA1R_VEO_SMOKE_ENABLED=false
```

Only `GEMINI_API_KEY` is accepted for this Gemini API credential. Readiness exposes presence as a boolean and always marks the value redacted.

Before any future submit: Runtime LTS, ProviderStackDriftGuard, CostEstimateSnapshot, HumanPaidRenderApproval, ProviderIdempotencyKey, PaidAttemptLimitGate, ProviderBoundaryGate, ChannelMonthlyBudgetGate and both kill switches must pass. The approval must scope exactly one 8-second output. Auto retry and provider failover are forbidden.

If Veo is unavailable, mark `AI_HERO_UNAVAILABLE`; use `NATIVE_VISUAL_REQUIRED` only when frozen policy permits, otherwise `REVIEW_REQUIRED` or `BLOCK`. Record original intent, reason, decision, resulting role, review need and cost avoided.

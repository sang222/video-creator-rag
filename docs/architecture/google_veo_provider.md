# Google Veo Provider Foundation

VCOS uses Google Veo through the official Google Gen AI SDK and native Gemini API boundary. The provider key is `google_veo`; transport is `GEMINI_API_NATIVE`. Veo is selective hero media, never the long-form visual backbone.

Layering:

```text
AssetRequestCompiler
  -> role=AI_HERO
  -> AIHeroAssetRequest
  -> frozen provider policy
  -> GoogleVeoGenerationRequest
  -> GoogleVeoAdapter
```

`GoogleVeoAdapter` validates configuration, compiles the SDK request, submits, polls bounded asynchronous operations, parses outcomes, creates a volatile output reference and builds/downloads an output plan. Real submit/poll requires both execution flags plus provider-boundary, human approval, cost, budget, attempt, idempotency and kill-switch gates. Defaults always produce `will_execute=false` and `provider_call_made=false`.

Async lifecycle is `PLANNED -> APPROVED -> SUBMITTING -> SUBMITTED -> PROCESSING -> SUCCEEDED`; terminal failures are `FAILED`, `MODERATED`, `TIMED_OUT`, `CANCELLED`, `OUTPUT_MISSING`, `DOWNLOAD_FAILED`, `CHECKSUM_FAILED`, and `QC_FAILED`. Polling and download retries never create another generation. Timeout does not resubmit.

The internal idempotency fingerprint binds provider/model, prompt hash, reference asset hashes, duration, resolution, aspect ratio, output count, project, scene and approval scope. An active or completed fingerprint returns the existing operation.

Price truth lives only in `config/google_veo_model_price_catalog.yaml`, version `2026-07-12`. Fast 720p is 0.10 USD/second, so one approved 8-second output estimates 0.80 USD. Estimated cost is not actual charged cost; actual remains null until real execution evidence exists.

Veo may return synchronized audio. Current policy records audio presence/stream metadata, then discards it during normalization. Durable manifests never persist signed/tokenized output URLs or `GEMINI_API_KEY`.

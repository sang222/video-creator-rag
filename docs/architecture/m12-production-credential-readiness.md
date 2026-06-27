# M12 Production Credential Onboarding + Real Provider Smoke

## Scope

M12 adds a production readiness layer for providers already designed in M10.1-M10.5:

- Credential/config readiness checks.
- Provider status aggregation for dashboard/API/CLI.
- Guarded real-smoke orchestration.
- Hard-env AI budget display.
- Secret redaction and safety checks.
- Vietnamese Settings / Integrations dashboard.

M12 does not generate production videos, upload/publish to YouTube, run real Cloud Final Renderer execution, or add a new provider strategy.

## Readiness Model

New runtime tables:

- `provider_readiness_checks`
- `provider_readiness_snapshots`
- `real_smoke_runs`

Checks use these states: `PASS`, `WARNING`, `BLOCKED`, `SKIPPED`, `FAILED`, `UNKNOWN`.

Snapshots use: `READY`, `PARTIAL`, `BLOCKED`, `UNKNOWN`.

Smoke runs use: `SKIPPED`, `RUNNING`, `PASS`, `FAILED`, `BLOCKED`.

## API / CLI

API:

- `GET /integrations/readiness`
- `POST /integrations/readiness/run`
- `GET /integrations/readiness/snapshots/{id}`
- `GET /integrations/providers/{provider_key}/readiness`
- `POST /integrations/providers/{provider_key}/smoke`
- `GET /integrations/smoke-runs/{id}`

CLI:

```bash
vcos integrations readiness
vcos integrations readiness --run-snapshot
vcos integrations smoke --provider ollama
vcos integrations smoke --provider youtube-public
vcos integrations smoke --provider youtube-owner
vcos integrations smoke --provider google-drive
vcos integrations smoke --provider google-vertex-veo
vcos integrations smoke --provider elevenlabs
vcos integrations smoke --provider creatomate
vcos integrations smoke --provider cloud-final-renderer
```

## Real Smoke Guard Policy

Default behavior is `SKIPPED`. Real calls require explicit env flags:

- Ollama: `VCOS_LLM_REAL_EXECUTION_ENABLED=true` and `VCOS_LLM_ROUTER_REAL_SMOKE=true`.
- YouTube public: `VCOS_YOUTUBE_REAL_PUBLIC_SMOKE=true`.
- YouTube owner analytics: `VCOS_YOUTUBE_REAL_OWNER_SMOKE=true`.
- Google Drive upload: `VCOS_DRIVE_REAL_UPLOAD_SMOKE=true`.
- Google Vertex Veo: `VCOS_VEO_REAL_EXECUTION_ENABLED=true` and `VCOS_VEO_REAL_SMOKE=true`.
- ElevenLabs: no real TTS is added in M12.
- Creatomate: no real render is added in M12.

If a smoke flag is enabled but credential/config is missing, M12 records `BLOCKED`, not fake success.

## Env Configuration

Use local `.env` or a secret manager. Do not commit real values.

Core budget display:

```bash
VCOS_BUDGET_MODE=hard_env
VCOS_MONTHLY_AI_BUDGET_USD=250
VCOS_LLM_MONTHLY_BUDGET_USD=
VCOS_LLM_BUDGET_NOTE=Local Ollama/router budget cap, display only.
```

ElevenLabs:

```bash
VCOS_VOICE_PROVIDER=elevenlabs
VCOS_ELEVENLABS_PLAN=creator
VCOS_ELEVENLABS_MONTHLY_CAP_USD=22
VCOS_ELEVENLABS_MONTHLY_CREDIT_CAP=121000
VCOS_ELEVENLABS_BUDGET_BASIS=credits_characters
ELEVENLABS_API_KEY=
```

Google Vertex Veo:

```bash
VCOS_AI_HERO_PROVIDER=google_vertex_veo
GOOGLE_CLOUD_PROJECT_ID=
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service_account.json
VCOS_VEO_MODEL_ID=veo-3.1-fast-generate-001
VCOS_VEO_COST_PER_SECOND_1080P=0.10
VCOS_VEO_MONTHLY_CAP_USD=75
VCOS_VEO_DEFAULT_DURATION_SECONDS=8
VCOS_VEO_MAX_DURATION_SECONDS=8
VCOS_VEO_REAL_EXECUTION_ENABLED=false
VCOS_VEO_REAL_SMOKE=false
```

Creatomate:

```bash
VCOS_RENDER_PROVIDER=creatomate
CREATOMATE_PLAN=growth_10k
CREATOMATE_MONTHLY_CREDITS=10000
CREATOMATE_MONTHLY_BUDGET_USD=149
CREATOMATE_API_KEY=
```

Disabled optional spend:

```bash
VCOS_STOCK_MONTHLY_BUDGET_USD=0
VCOS_MUSIC_SFX_MONTHLY_BUDGET_USD=0
VCOS_EXTRA_AI_IMAGE_MONTHLY_BUDGET_USD=0
```

## YouTube OAuth

Configure readonly scopes only:

```bash
YOUTUBE_OWNER_ANALYTICS_ENABLED=true
YOUTUBE_OAUTH_CLIENT_SECRETS_FILE=/absolute/path/to/client_secret.json
YOUTUBE_OAUTH_SCOPES=https://www.googleapis.com/auth/youtube.readonly,https://www.googleapis.com/auth/yt-analytics.readonly
```

Start OAuth from dashboard CTA or:

```bash
open http://localhost:8000/auth/youtube/start
```

Token files remain under ignored local dev credential storage. Raw tokens are not stored in plain DB fields.

## Google Drive OAuth

Use `drive.file` only:

```bash
GOOGLE_DRIVE_OFFLOAD_ENABLED=true
GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS_FILE=/absolute/path/to/client_secret.json
GOOGLE_DRIVE_OAUTH_SCOPES=https://www.googleapis.com/auth/drive.file
GOOGLE_DRIVE_ROOT_FOLDER_ID=
```

Start OAuth from dashboard CTA or:

```bash
open http://localhost:8000/auth/google-drive/start
```

Real upload smoke uploads a tiny test file only when `VCOS_DRIVE_REAL_UPLOAD_SMOKE=true`.

## Cloud Final Renderer Readiness

Cloud Final Renderer is `READY_FOR_SMOKE` when:

```bash
CLOUD_FINAL_RENDERER_PROVIDER=creatomate
CREATOMATE_PLAN=growth_10k
CREATOMATE_API_KEY=<secret-manager-or-local-env>
```

If provider or plan is missing, status is `NEEDS_CONFIG`. If `CREATOMATE_API_KEY` is missing, status is `NEEDS_CREDENTIAL`.

M12 still does not run real long-form rendering by default. The dashboard and API only validate readiness/configuration and guarded smoke state.

## Security

- API/UI/CLI do not expose raw secret values.
- `real_smoke_runs.env_flags` stores only booleans/redacted presence.
- Service account paths are redacted in dashboard payloads.
- Frontend does not use localStorage for tokens/secrets.
- M12 does not add plain DB secret fields.

## Dashboard

Routes:

- `/settings`
- `/settings/integrations`
- `/providers/readiness`

The page title is `Cấu hình tích hợp`. Technical details are collapsed. The budget section is `Ngân sách AI tháng này` and explicitly states that values are hard env caps, not actual spend.

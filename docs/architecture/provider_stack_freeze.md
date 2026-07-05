# DX2 / R3D10 Provider Stack Freeze

DX2 khóa provider truth cho R3D9 Provider/Cost/Readiness panel. R3D10 giữ nguyên stack này trong Runtime LTS v1.

## Active Provider Keys

- `elevenlabs` / ElevenLabs: voice/TTS only.
- `luma_api` / Luma API: AI hero/metaphor video only.
- `creatomate_growth_10k` / Creatomate Growth 10K: final assembly + template/card/thumbnail/Shorts renderer.
- `pexels_api` / Pexels API: free visual fallback only.

## Manual / Storage Boundary

- YouTube: manual publish + read-only analytics/verification. No YouTube upload API.
- Drive/object storage: optional archive/later. No Drive upload in DX2/R3D9/R3D10.
- Post-freeze Drive archive smoke, when explicitly enabled, treats `GOOGLE_DRIVE_ROOT_FOLDER_ID` as the archive root and writes relative folders below it only. New unscoped smoke writes use `smoke_tests/YYYY-MM-DD`; new project-scoped archive writes use `company_{company_id}/channel_{channel_workspace_id}/project_{video_project_id}/{media_type_or_subfolder}`. Historical `VCOS/company_unknown/channel_unknown/project_unknown` folders may be cleaned manually if desired; VCOS does not move/delete old Drive folders automatically.

## Deferred / Inactive

- `GOOGLE_VERTEX_VEO` / `google-vertex-veo` / Veo: deferred compatibility only, not active.
- Runway: deferred.
- DaVinci: manual/workbench only, not API core.
- Envato: avoided.
- Adobe/Shutterstock/paid stock: deferred.
- `creatomate_essential_2k`: inactive; not final renderer.
- `cloud_final_assembly_renderer_tbd`: inactive; not a required active gap.
- `pexels_pixabay_free_fallback`: inactive; use `pexels_api`.

## Luma Duration Spec

- Max duration: 8 seconds.
- Allowed durations: `4`, `6`, `8`.
- Do not open 10 seconds unless a future budget/provider freeze confirms it.

## Creatomate Templates

- `CREATOMATE_TEMPLATE_ID` remains the MVP default template env.
- Future activation may split granular IDs:
  - `CREATOMATE_LONG_FORM_TEMPLATE_ID`
  - `CREATOMATE_SHORTS_TEMPLATE_ID`
  - `CREATOMATE_THUMBNAIL_TEMPLATE_ID`
  - `CREATOMATE_TITLE_CARD_TEMPLATE_ID`
  - `CREATOMATE_HERO_COMPOSITION_TEMPLATE_ID`
- DX2 does not require all granular IDs before real provider activation unless later tests/config make them mandatory.

## Runtime Boundary

M2 is readiness/wiring only. R3D8 is validation/firewall only by default. R3D9 is ops/read-model only. DX2 does not add provider execution, media generation, render submission, Pexels search/download, Drive upload, or YouTube upload.

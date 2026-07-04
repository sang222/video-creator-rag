# DX2 Provider Stack Freeze

DX2 khóa provider truth cho R3D9 Provider/Cost/Readiness panel. Đây là read/write source-of-truth kỹ thuật cho provider stack hiện hành.

## Active Provider Keys

- `elevenlabs`: voice/TTS only.
- `luma_api`: AI hero/metaphor video only.
- `creatomate_growth_10k`: final assembly + template/card/thumbnail/Shorts renderer.
- `pexels_api`: free visual fallback only.

## Manual / Storage Boundary

- YouTube: manual publish; analytics/read-only later. No YouTube upload API.
- Drive/object storage: archive optional/later. No Drive upload in DX2/R3D9.

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

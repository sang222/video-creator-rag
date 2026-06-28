# Company Bootstrap Report

**Verdict:** ✅ PASS

## Metadata

| Item | Value |
|------|-------|
| Repo path | `/Users/sangss/Desktop/video-creator-rag` |
| DB connection | `postgresql+psycopg://vcos:vcos@localhost:55432/vcos` |
| Existing company count before | 0 |

## Result

| Item | Value |
|------|-------|
| Company created | ✅ Small Team AI |
| Company slug | `small-team-ai` |
| Company UUID | `e0b7c806-b39e-4792-bf2e-7e8c6d6ca464` |
| Idempotent | ✅ Re-running returns same UUID |
| Description | Company workspace for Small Team AI YouTube operations. |
| Status | active |
| Default currency | USD |

## Changes Made

### Model — `app/db/models/foundation.py`
- Added `slug: Mapped[str]` (String(128), NOT NULL, UNIQUE) to `Company`
- Added `description: Mapped[str]` (Text, NOT NULL, default="") to `Company`

### Migration — `alembic/versions/b35988076109_add_company_slug_description.py`
- Adds `slug` column (nullable first → backfill → NOT NULL + unique constraint)
- Adds `description` column (nullable first → backfill → NOT NULL, server default '')
- Creates `uq_companies_slug` unique constraint
- Applied successfully to local DB

### Service — `app/services/company.py`
- `create_company()` now requires `slug`, optional `description`; idempotent by slug (returns existing if slug matches)
- Added `get_company_by_slug(slug)` method
- `list_companies()` unchanged (already existed)

### API — `app/main.py`
- Updated `CompanyCreate` schema: added `slug`, `description`
- Updated `CompanyRead` schema: added `slug`, `description`
- Updated `_company()` helper: includes `slug`, `description`
- Updated `POST /companies` handler: passes `slug`, `description`
- Added `GET /companies` endpoint (list all companies)

### CLI — `app/cli/main.py`
- Updated `vcos company create`: added `--slug` (required), `--description` (optional)
- Added `vcos company list`: lists id, name, slug, description, status, default_currency, created_at
- Added `vcos company bootstrap --name --slug [--description] [--default-currency]`: idempotent, creates or returns existing, prints `COMPANY_ID=<uuid>`

## Tests Run

```
tests/test_company_bootstrap.py — 10/10 passed

- test_create_returns_uuid ✅
- test_create_with_description ✅
- test_create_default_values ✅
- test_idempotent_by_slug ✅
- test_list_includes_created ✅
- test_list_default_limit ✅
- test_get_by_slug_found ✅
- test_get_by_slug_not_found ✅
- test_duplicate_slug_no_error ✅
- test_no_channel_row ✅
```

## Scope Not Built

- ❌ No Channel Init form change
- ❌ No channel workflow change
- ❌ No channel created
- ❌ No Channel Contract compiled
- ❌ No M12.2S run
- ❌ No provider call
- ❌ No old provider smoke
- ❌ No upload/publish/reupload
- ❌ No commit/tag

## Provider Calls Avoided

Zero provider calls made. Bootstrap only touches the local PostgreSQL DB.

## Next Action

Copy `COMPANY_ID=e0b7c806-b39e-4792-bf2e-7e8c6d6ca464` into **Dashboard → Tạo kênh → ID công ty**.
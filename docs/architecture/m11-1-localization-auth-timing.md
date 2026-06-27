# M11.1 Localization / Auth / Publish Timing

M11.1 mở rộng Operator Dashboard thành cockpit tiếng Việt có login local/dev, gói phụ đề/metadata theo ngôn ngữ, readiness gate localization, và khung giờ publish theo timezone kênh.

## Scope

- Dashboard operator UI mặc định tiếng Việt.
- `/login` local password auth shell.
- Bootstrap admin đầu tiên từ env, password lưu hash, session dùng httpOnly cookie.
- Channel localization config: primary language/region/timezone, target subtitle/metadata languages, target regions, translation mode, required flags.
- `localized_subtitle_packages` dùng CloudMediaRef/Google Drive CTA cho SRT/VTT khi có file.
- `localized_metadata_packages` lưu title/description/tags theo ngôn ngữ, bắt buộc human review trước khi dùng.
- Localization readiness gate trả PASS/REVIEW_REQUIRED/BLOCK/NOT_REQUIRED với summary tiếng Việt.
- Channel publish timing policy lưu IANA timezone và khung giờ publish đã cấu hình.
- Publish timing suggestion chuyển local target timezone sang UTC và giờ operator.
- Publish handoff/uploaded video dashboard hiển thị subtitle/metadata/timing state.

## Non-Scope

- Không auto publish/upload/reupload.
- Không YouTube upload API.
- Không re-upload theo quốc gia.
- Không auto translate/publish khi chưa human review.
- Không AI dubbing.
- Không TikTok/Facebook analytics loop.
- Không backend Drive download/preview proxy.
- Không expose local file path.
- Không production SSO/2FA/password reset.
- Không config upgrade suggestion.
- Không fake traffic/bot engagement/platform evasion.

## Auth Env

```env
VCOS_DASHBOARD_AUTH_ENABLED=true
VCOS_AUTH_MODE=local_password
VCOS_BOOTSTRAP_ADMIN_EMAIL=admin@local.vcos
VCOS_BOOTSTRAP_ADMIN_PASSWORD=<human-set-local-password>
VCOS_BOOTSTRAP_ADMIN_ROLE=OWNER_ADMIN
```

Nếu đã có operator user, bootstrap env bị bỏ qua. Không có `admin/admin` default.

## Dashboard Language

Operator-facing UI dùng tiếng Việt. Raw enum, reason code, audit id, source ref và technical id chỉ thuộc phần chi tiết kỹ thuật/các appendix.

## Core Product Rule

Một canonical YouTube video có thể có nhiều subtitle/metadata language pack. VCOS không tạo nhiều bản re-upload theo quốc gia.

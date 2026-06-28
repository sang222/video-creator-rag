# M12.2P-R Channel Activation CTA Repair

* Verdict: PASS
* Repo path: `/Users/sangss/Desktop/video-creator-rag`

## Channel activation UI change

* Tab `Hồ sơ & chính sách kênh` hiển thị nút `Kích hoạt kênh` khi channel còn `DRAFT`, có latest policy snapshot, Channel Contract `COMPLETE`.
* Click gọi `POST /channels/{channel_id}/activate` qua `activateChannel(channelId)`.
* Sau thành công: hiện thông báo `Kênh đã được kích hoạt. Các project mới sẽ dùng snapshot hiện tại.` và refresh channel detail.
* Khi bị chặn: hiển thị lỗi tiếng Việt, không fake success.

## Backend activation behavior

* Activation vẫn yêu cầu Channel Contract `COMPLETE` và compiled policy snapshot.
* Activation chỉ đổi lifecycle/status sau action explicit từ operator.
* Không mutate nội dung `ChannelProfileVersion.profile_input` hoặc hash.
* Không mutate `VideoProject.policy_snapshot_id` đã tồn tại.
* Ghi audit/event:
  * `CHANNEL_ACTIVATED`
  * `CHANNEL_ACTIVATION_BLOCKED`

## Tests run

* PASS: `PYTHONPATH=. .venv/bin/python -m py_compile tests/test_channel_activation_cta.py app/services/channel_profile.py`
* PASS: `PYTHONPATH=. .venv/bin/pytest -q tests/test_channel_activation_cta.py`
* PASS: `PYTHONPATH=. .venv/bin/python -m compileall app`
* PASS: `PYTHONPATH=. .venv/bin/pytest -q tests -k "channel_activation or channel_contract or channel_init"`
* PASS: `git diff --check`
* PASS: `cd frontend && npm run lint`
* PASS: `cd frontend && npm run test`
* PASS: `cd frontend && npm run build`
* PASS: `cd frontend && npm run typecheck` sau build sinh lại `.next/types`.

## Scope not built

* Không tạo channel mới.
* Không chạy M12.2S.
* Không gọi provider thật.
* Không chạy old provider smoke.
* Không upload/publish/reupload.
* Không auto-activate.
* Không commit/tag.

## Next action

Kích hoạt `Small Team AI`, sau đó rerun M12.2S.

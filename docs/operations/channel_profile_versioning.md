# Channel profile versioning

Use the existing channel workspace, API and Vietnamese operator cockpit. There is no separate multi-channel admin product and no provider execution control on this surface.

## Safe lifecycle

1. Read the active version and its capability state.
2. Create a draft from the active effective policy.
3. Edit only typed draft fields.
4. Validate and preview compile; preview persists nothing.
5. Review the semantic diff and capability blockers.
6. Compile a new immutable snapshot.
7. Submit the compiled version for approval.
8. Approve with an explicit operator approval reference, or reject with a reason.
9. Activate an approved snapshot for future projects.

Rollback uses the same activation operation on an older approved snapshot. It changes only which snapshot future projects receive. Active profile content, historical snapshots and existing project bindings are never edited.

## API surface

- `GET /channels/{channel_id}/profile-management`
- `GET /channels/{channel_id}/profile-versions`
- `GET /channels/{channel_id}/profile-versions/active`
- `POST /channels/{channel_id}/profile-versions/draft-from-active`
- `PUT /profile-versions/{id}/draft`
- `POST /profile-versions/{id}/validate`
- `POST /profile-versions/{id}/preview-compile`
- `GET /profile-versions/{id}/diff/{other_id}`
- `POST /profile-versions/{id}/compile`
- `POST /profile-versions/{id}/submit-for-approval`
- `POST /profile-versions/{id}/approve`
- `POST /profile-versions/{id}/reject`
- `POST /policy-snapshots/{snapshot_id}/activate`

An active scoped version cannot compile to changed content. Create a draft instead. Activation rejects an unapproved snapshot or any capability blocker.

## Current first-channel evidence

`small-team-ai` profile v1 uses approval `operator-approval://ch1-flex/small-team-ai/profile-v1`. Its policy keeps ElevenLabs narration plus Forced Alignment, `CanonicalMediaTimeline`, NativeFFmpeg final render, optional Pexels/Veo roles, Drive verification before cleanup and manual YouTube upload. CH1-FLEX performs no provider, render, archive or publish execution.

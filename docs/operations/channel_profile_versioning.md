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

## CH1-FLEX v2 bounded lifecycle

The v2 lifecycle uses `ChannelProfileService.approve_and_activate_ch1_flex_v2()` for the exact `small-team-ai` transition. It:

1. reads immutable v1 profile/snapshot content;
2. reuses the existing mutable version-2 draft, or creates version 2 only when that slot is absent and no later version exists;
3. applies the typed VSR1/Gemini Image/VQC1/canary/Drive overlay and scoped operator approval ref;
4. validates two deterministic preview hashes and records the semantic diff;
5. compiles, approves, and activates the exact snapshot;
6. returns immutable receipt IDs and a rollback pointer to v1.

The helper blocks a version collision, a mismatched immutable v2, incomplete qualification evidence, or any v1 content change. It creates no provider attempt, media/render job, Drive upload, or publish action.

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

## First-channel policy lineage

`small-team-ai` profile v1 uses approval `operator-approval://ch1-flex/small-team-ai/profile-v1` and remains the immutable rollback baseline. The v2 policy preserves ElevenLabs narration plus Forced Alignment, `CanonicalMediaTimeline`, NativeFFmpeg final render, Drive verification before cleanup, and manual YouTube upload while adding qualified `STOCK_ASSISTED` visual governance and NICH1 policy truth. Profile lifecycle work itself performs no provider, render, archive, or publish execution.

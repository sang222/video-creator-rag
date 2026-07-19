from __future__ import annotations

import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    CloudMediaRef,
    FinalMediaRef,
    HumanUploadTask,
    LLMRunSnapshot,
    MediaOffloadJob,
    MediaRenderJob,
    PaidProviderCallLedger,
    ProviderAttempt,
    ProviderJobSnapshot,
    UploadedVideo,
)
from app.db.models.channel import ChannelProfileVersion, ChannelWorkspace
from app.db.session import session_scope
from app.services.channel_profile import ChannelProfileService


CHANNEL_KEY = "small-team-ai"
APPROVAL_REF = (
    "operator-approval://ch1-flex-v2/small-team-ai/master-prompt-2026-07-19"
)
EXPECTED_PROFILE_V1_ID = uuid.UUID("f5e45981-51eb-4c24-95a8-f9f5db761195")
EXPECTED_DRAFT_V2_ID = uuid.UUID("d735ec40-d29f-4d73-9e8a-58b4e1bfe325")
EXPECTED_SNAPSHOT_V1_ID = uuid.UUID("f9201609-faad-4b68-aebf-b56679d0bde6")
EXPECTED_SNAPSHOT_V1_HASH = (
    "df3abe8096f8e430520f6a6860fdc27a2a48c12f68fcb3da43c5f8df46a1999a"
)


def _row_count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _execution_counts(session: Session) -> dict[str, int]:
    return {
        "provider_attempts": _row_count(session, ProviderAttempt),
        "provider_job_snapshots": _row_count(session, ProviderJobSnapshot),
        "paid_provider_call_ledgers": _row_count(session, PaidProviderCallLedger),
        "llm_run_snapshots": _row_count(session, LLMRunSnapshot),
        "media_render_jobs": _row_count(session, MediaRenderJob),
        "final_media_refs": _row_count(session, FinalMediaRef),
        "media_offload_jobs": _row_count(session, MediaOffloadJob),
        "cloud_media_refs": _row_count(session, CloudMediaRef),
        "human_upload_tasks": _row_count(session, HumanUploadTask),
        "uploaded_videos": _row_count(session, UploadedVideo),
    }


def _deltas(
    before: dict[str, int], after: dict[str, int]
) -> dict[str, int]:
    return {key: after[key] - before[key] for key in before}


def _validate_entry(session: Session, channel: ChannelWorkspace) -> None:
    versions = {
        item.version: item
        for item in session.scalars(
            select(ChannelProfileVersion).where(
                ChannelProfileVersion.channel_workspace_id == channel.id
            )
        )
    }
    profile_v1 = versions.get(1)
    profile_v2 = versions.get(2)
    if profile_v1 is None or profile_v1.id != EXPECTED_PROFILE_V1_ID:
        raise RuntimeError("CH1_FLEX_V1_ENTRY_PROFILE_MISMATCH")
    if profile_v2 is None or profile_v2.id != EXPECTED_DRAFT_V2_ID:
        raise RuntimeError("CH1_FLEX_V2_DRAFT_ENTRY_MISMATCH")
    if profile_v2.status != "draft":
        raise RuntimeError("CH1_FLEX_V2_DRAFT_NOT_MUTABLE")
    if channel.active_policy_snapshot_id != EXPECTED_SNAPSHOT_V1_ID:
        raise RuntimeError("CH1_FLEX_V1_ACTIVE_SNAPSHOT_MISMATCH")
    snapshot_v1 = ChannelProfileService(session)._latest_snapshot_for_profile(
        profile_v1.id
    )
    if (
        snapshot_v1 is None
        or snapshot_v1.id != EXPECTED_SNAPSHOT_V1_ID
        or snapshot_v1.content_hash != EXPECTED_SNAPSHOT_V1_HASH
    ):
        raise RuntimeError("CH1_FLEX_V1_IMMUTABLE_SNAPSHOT_MISMATCH")


def main() -> int:
    with session_scope() as session:
        channel = session.scalar(
            select(ChannelWorkspace).where(ChannelWorkspace.key == CHANNEL_KEY)
        )
        if channel is None:
            raise RuntimeError("CH1_FLEX_CHANNEL_NOT_FOUND")
        _validate_entry(session, channel)
        before = _execution_counts(session)
        result = ChannelProfileService(session).approve_and_activate_ch1_flex_v2(
            channel_id=channel.id,
            approved_by=None,
            approval_ref=APPROVAL_REF,
            correlation_id="ch1-flex-v2-master-2026-07-19",
        )
        session.flush()
        after = _execution_counts(session)
        deltas = _deltas(before, after)
        if any(deltas.values()):
            raise RuntimeError(f"CH1_FLEX_V2_EXECUTION_DELTA_NONZERO:{deltas}")
        output = {
            **result,
            "approval_ref": APPROVAL_REF,
            "execution_counts_before": before,
            "execution_counts_after": after,
            "execution_count_deltas": deltas,
            "master_provider_calls": 0,
            "master_media_render_calls": 0,
            "master_drive_calls": 0,
            "master_youtube_calls": 0,
            "mr1_execution": "ON_HOLD",
            "proceed_to_mr1": False,
        }
        session.commit()

    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

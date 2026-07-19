from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import DestinationBinding, TargetMarketProfile
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
from app.db.models.channel import ChannelProfileVersion, ChannelWorkspace, CompiledChannelPolicySnapshot
from app.db.session import session_scope
from app.services import ChannelProfileService, ConfigRegistryService, TargetMarketDigestCompiler


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_KEY = "small-team-ai"
APPROVAL_REF = "operator-approval://ch1-market-v3/small-team-ai/master-prompt-2026-07-19"
EXPECTED_PROFILE_V2_ID = uuid.UUID("d735ec40-d29f-4d73-9e8a-58b4e1bfe325")
EXPECTED_SNAPSHOT_V2_ID = uuid.UUID("6304e2a4-f096-410b-af09-a2748b311855")
EXPECTED_SNAPSHOT_V2_HASH = "3b7b2bf83efae2daf93a8d92f6d0afe21ca1a3c96ab1ce2f3744a5bf93574e46"


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


def _validate_reports() -> None:
    requirements = {
        "geo1_summary.json": ("verdicts", "GEO1_FINAL", "PASS"),
        "geo2_summary.json": ("verdicts", "GEO2_FINAL", "PASS"),
        "ch1_flex_v2_summary.json": ("verdicts", "CH1_FLEX_V2_FINAL", "PASS"),
    }
    for filename, (section, key, expected) in requirements.items():
        payload = json.loads((ROOT / "reports" / filename).read_text(encoding="utf-8"))
        if payload.get(section, {}).get(key) != expected:
            raise RuntimeError(f"CH1_MARKET_V3_ENTRY_REPORT_INVALID:{filename}:{key}")
    lpro = json.loads((ROOT / "reports" / "lpro1_summary.json").read_text(encoding="utf-8"))
    if lpro.get("result") != "PASS":
        raise RuntimeError("CH1_MARKET_V3_ENTRY_REPORT_INVALID:lpro1_summary.json:result")


def _validate_v2_entry(session: Session, channel: ChannelWorkspace) -> None:
    profile_v2 = session.get(ChannelProfileVersion, EXPECTED_PROFILE_V2_ID)
    snapshot_v2 = session.get(CompiledChannelPolicySnapshot, EXPECTED_SNAPSHOT_V2_ID)
    if (
        profile_v2 is None
        or profile_v2.channel_workspace_id != channel.id
        or profile_v2.version != 2
        or profile_v2.status != "active"
        or snapshot_v2 is None
        or snapshot_v2.channel_profile_version_id != profile_v2.id
        or snapshot_v2.status != "active"
        or snapshot_v2.content_hash != EXPECTED_SNAPSHOT_V2_HASH
        or channel.active_policy_snapshot_id != snapshot_v2.id
    ):
        raise RuntimeError("CH1_MARKET_V3_ACTIVE_V2_ENTRY_MISMATCH")


def _market_profile(channel: ChannelWorkspace) -> TargetMarketProfile:
    loaded = ConfigRegistryService(None).validate_catalog(ROOT / "config" / "target_market_profile_catalog.yaml")
    item = next(row for row in loaded.content["items"] if row["key"] == "small-team-ai-us-market")
    semantic_fields = set(TargetMarketProfile.model_fields) - {
        "schema_version",
        "profile_version",
        "channel_id",
        "approval_ref",
        "approved_draft_ref",
        "content_hash",
    }
    return TargetMarketProfile(
        **{key: item[key] for key in semantic_fields},
        profile_version=1,
        channel_id=channel.id,
        approval_ref=APPROVAL_REF,
        approved_draft_ref="offline-fixture://target-market/small-team-ai-us-market/v1",
    )


def main() -> int:
    _validate_reports()
    with session_scope() as session:
        channel = session.scalar(select(ChannelWorkspace).where(ChannelWorkspace.key == CHANNEL_KEY))
        if channel is None:
            raise RuntimeError("CH1_MARKET_V3_CHANNEL_NOT_FOUND")
        _validate_v2_entry(session, channel)
        profile = _market_profile(channel)
        digest = TargetMarketDigestCompiler().compile(profile)
        destination = DestinationBinding(
            binding_version=1,
            channel_id=channel.id,
            channel_key=channel.key,
            platform="YOUTUBE",
            channel_handle="@SmallTeamAI",
            account_country=None,
            target_market_profile_ref=digest.profile_ref,
            target_market_profile_hash=str(profile.content_hash),
            target_market="US",
            primary_market="US",
            primary_locale="en-US",
            original_language="en",
            default_visibility="PRIVATE",
            manual_publish_required=True,
            destination_status="PENDING_PLATFORM_ID",
            verification_state="PENDING",
            approval_ref=APPROVAL_REF,
        )
        before = _execution_counts(session)
        result = ChannelProfileService(session).approve_and_activate_ch1_market_v3(
            channel_id=channel.id,
            target_market_profile=profile,
            target_market_digest=digest,
            destination_binding=destination,
            approval_ref=APPROVAL_REF,
            approved_by=None,
            correlation_id="ch1-market-v3-master-2026-07-19",
        )
        session.flush()
        after = _execution_counts(session)
        deltas = {key: after[key] - before[key] for key in before}
        if any(deltas.values()):
            raise RuntimeError(f"CH1_MARKET_V3_EXECUTION_DELTA_NONZERO:{deltas}")
        output = {
            **result,
            "approval_ref": APPROVAL_REF,
            "account_country": None,
            "account_country_source": "UNAVAILABLE_NOT_INVENTED",
            "execution_counts_before": before,
            "execution_counts_after": after,
            "execution_count_deltas": deltas,
            "mr1_execution": "ON_HOLD",
            "proceed_to_mr1": False,
        }
        session.commit()
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.db.models import (
    AnalyticsSyncRun,
    CostEvent,
    CredentialHealthSnapshot,
    CredentialReference,
    LLMRunSnapshot,
    MediaRenderJob,
    ProductionArtifactRun,
    ProviderAttempt,
    ProviderHealthSnapshot,
    ProviderRegistryEntry,
    QuotaAccount,
    QuotaEvent,
    RetryPolicy,
    ChannelDailyRun,
)


PURGED_PROVIDER_KEY = "purged_mock_runtime"
PURGED_RENDERER_KEY = "REAL_DISABLED"
PURGE_REASON = "PURGED_MOCK_RUNTIME"


class MockRuntimePurgeService:
    def __init__(self, session: Session):
        self.session = session

    def preview(self) -> dict[str, Any]:
        return {"apply": False, "before": self._counts(), "after": None}

    def apply(self) -> dict[str, Any]:
        before = self._counts()
        self._disable_provider_rows()
        self._block_analytics_runs()
        self._quarantine_llm_snapshots()
        self.session.flush()
        self._quarantine_append_only_mock_rows()
        self._quarantine_provider_refs()
        self._block_daily_runs()
        self._block_production_runs()
        self._block_render_jobs()
        self.session.flush()
        after = self._counts()
        return {"apply": True, "before": before, "after": after}

    def _counts(self) -> dict[str, int]:
        return {
            "active_mock_providers": self._count(
                select(func.count())
                .select_from(ProviderRegistryEntry)
                .where(_mock_provider_key(ProviderRegistryEntry.provider_key))
                .where(ProviderRegistryEntry.status.in_(["ACTIVE", "EXPERIMENTAL"]))
            ),
            "mock_provider_attempts": self._count(
                select(func.count()).select_from(ProviderAttempt).where(_mock_provider_key(ProviderAttempt.provider_key))
            ),
            "mock_llm_snapshots": self._count(
                select(func.count())
                .select_from(LLMRunSnapshot)
                .where(
                    or_(
                        _mock_provider_key(LLMRunSnapshot.provider_key),
                        func.lower(LLMRunSnapshot.model_key).like("mock%"),
                        func.lower(LLMRunSnapshot.model_name).like("mock%"),
                        LLMRunSnapshot.run_mode == "MOCK",
                    )
                )
            ),
            "mock_channel_daily_runs": self._count(
                select(func.count()).select_from(ChannelDailyRun).where(ChannelDailyRun.run_mode == "MOCK")
            ),
            "mock_production_runs": self._count(
                select(func.count()).select_from(ProductionArtifactRun).where(ProductionArtifactRun.run_mode.in_(["MOCK", "LOCAL_FIXTURE"]))
            ),
            "local_ffmpeg_render_jobs": self._count(
                select(func.count()).select_from(MediaRenderJob).where(MediaRenderJob.renderer_key == "LOCAL_FFMPEG")
            ),
            "mock_analytics_runs": self._count(
                select(func.count())
                .select_from(AnalyticsSyncRun)
                .where(or_(AnalyticsSyncRun.sync_mode == "MOCK", _mock_provider_key(AnalyticsSyncRun.provider_key)))
            ),
        }

    def _disable_provider_rows(self) -> None:
        for row in self.session.scalars(select(ProviderRegistryEntry).where(_mock_provider_key(ProviderRegistryEntry.provider_key))):
            row.status = "DISABLED"
            row.metadata_ = _with_purge_marker(row.metadata_)

    def _quarantine_provider_refs(self) -> None:
        for row in self.session.scalars(select(CredentialReference).where(_mock_provider_key(CredentialReference.provider_key))):
            row.provider_key = PURGED_PROVIDER_KEY
            row.status = "DISABLED"
            row.metadata_ = _with_purge_marker(row.metadata_)
        for row in self.session.scalars(select(QuotaAccount).where(_mock_provider_key(QuotaAccount.provider_key))):
            row.provider_key = PURGED_PROVIDER_KEY
            row.status = "DISABLED"
            row.metadata_ = _with_purge_marker(row.metadata_)
        for row in self.session.scalars(select(RetryPolicy).where(_mock_provider_key(RetryPolicy.provider_key))):
            row.provider_key = PURGED_PROVIDER_KEY
            row.status = "DISABLED"

    def _quarantine_append_only_mock_rows(self) -> None:
        with self._disabled_trigger("provider_attempts"):
            for row in self.session.scalars(select(ProviderAttempt).where(_mock_provider_key(ProviderAttempt.provider_key))):
                row.provider_key = PURGED_PROVIDER_KEY
                row.status = "CANCELLED"
                row.error_code = PURGE_REASON
                row.error_message_redacted = "Mock provider attempt purged from production-active runtime."
                row.metadata_ = _with_purge_marker(row.metadata_)
            self.session.flush()
        with self._disabled_trigger("cost_events"):
            for row in self.session.scalars(select(CostEvent).where(_mock_provider_key(CostEvent.provider_key))):
                row.provider_key = PURGED_PROVIDER_KEY
                row.provider_run_ref = PURGE_REASON
                row.metadata_ = _with_purge_marker(row.metadata_)
            self.session.flush()
        with self._disabled_trigger("quota_events"):
            for row in self.session.scalars(select(QuotaEvent).where(_mock_provider_key(QuotaEvent.provider_key))):
                row.provider_key = PURGED_PROVIDER_KEY
                row.reason_code = row.reason_code or PURGE_REASON
                row.metadata_ = _with_purge_marker(row.metadata_)
            self.session.flush()
        with self._disabled_trigger("provider_health_snapshots"):
            for row in self.session.scalars(select(ProviderHealthSnapshot).where(_mock_provider_key(ProviderHealthSnapshot.provider_key))):
                row.provider_key = PURGED_PROVIDER_KEY
                row.reason_codes = _append_reason(row.reason_codes)
                row.metadata_ = _with_purge_marker(row.metadata_)
            self.session.flush()
        with self._disabled_trigger("credential_health_snapshots"):
            for row in self.session.scalars(select(CredentialHealthSnapshot).where(_mock_provider_key(CredentialHealthSnapshot.provider_key))):
                row.provider_key = PURGED_PROVIDER_KEY
                row.reason_codes = _append_reason(row.reason_codes)
                row.metadata_ = _with_purge_marker(row.metadata_)
            self.session.flush()

    def _quarantine_llm_snapshots(self) -> None:
        statement = select(LLMRunSnapshot).where(
            or_(
                _mock_provider_key(LLMRunSnapshot.provider_key),
                func.lower(LLMRunSnapshot.model_key).like("mock%"),
                func.lower(LLMRunSnapshot.model_name).like("mock%"),
                LLMRunSnapshot.run_mode == "MOCK",
            )
        )
        for row in self.session.scalars(statement):
            row.provider = "purged"
            row.provider_key = PURGED_PROVIDER_KEY
            row.model_name = "PURGED_MOCK_MODEL"
            row.model_key = "PURGED_MOCK_MODEL"
            row.run_mode = "REAL_DISABLED"
            row.status = "BLOCKED"
            row.cost_event_id = None
            row.quota_event_id = None
            row.cost_payload = _with_purge_marker(row.cost_payload or {})

    def _block_daily_runs(self) -> None:
        for row in self.session.scalars(select(ChannelDailyRun).where(ChannelDailyRun.run_mode == "MOCK")):
            row.run_mode = "REAL_DISABLED"
            row.status = "BLOCKED"
            row.reason_codes = _append_reason(row.reason_codes)
            row.metadata_ = _with_purge_marker(row.metadata_)

    def _block_production_runs(self) -> None:
        for row in self.session.scalars(select(ProductionArtifactRun).where(ProductionArtifactRun.run_mode.in_(["MOCK", "LOCAL_FIXTURE"]))):
            row.run_mode = "REAL_DISABLED"
            row.status = "BLOCKED"
            row.reason_codes = _append_reason(row.reason_codes)
            row.metadata_ = _with_purge_marker(row.metadata_)

    def _block_render_jobs(self) -> None:
        for row in self.session.scalars(select(MediaRenderJob).where(MediaRenderJob.renderer_key == "LOCAL_FFMPEG")):
            row.renderer_key = PURGED_RENDERER_KEY
            row.status = "BLOCKED"
            row.error_code = PURGE_REASON
            row.error_message_redacted = "Local fixture renderer job purged from production-active runtime."
            row.reason_codes = _append_reason(row.reason_codes)
            row.metadata_ = _with_purge_marker(row.metadata_)

    def _block_analytics_runs(self) -> None:
        statement = select(AnalyticsSyncRun).where(or_(AnalyticsSyncRun.sync_mode == "MOCK", _mock_provider_key(AnalyticsSyncRun.provider_key)))
        for row in self.session.scalars(statement):
            row.sync_mode = "REAL_DISABLED"
            row.sync_state = "BLOCKED"
            row.provider_key = PURGED_PROVIDER_KEY if row.provider_key else None
            row.provider_attempt_id = None
            row.reason_codes = _append_reason(row.reason_codes)
            row.next_action = "Configure YouTube analytics credentials or import manual/CSV analytics."
            row.metadata_ = _with_purge_marker(row.metadata_)

    def _count(self, statement) -> int:
        return int(self.session.scalar(statement) or 0)

    def _disabled_trigger(self, table_name: str):
        return _DisabledTrigger(self.session, table_name)


class _DisabledTrigger:
    def __init__(self, session: Session, table_name: str):
        self.session = session
        self.table_name = table_name
        self.trigger_name = f"trg_prevent_{table_name}_change"

    def __enter__(self) -> None:
        self.session.execute(text(f"ALTER TABLE {self.table_name} DISABLE TRIGGER {self.trigger_name}"))

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.session.execute(text(f"ALTER TABLE {self.table_name} ENABLE TRIGGER {self.trigger_name}"))

def _mock_provider_key(column):
    return func.lower(column).like("mock\\_%", escape="\\")


def _append_reason(reasons: list[str] | None) -> list[str]:
    result = list(reasons or [])
    if PURGE_REASON not in result:
        result.append(PURGE_REASON)
    return result


def _with_purge_marker(value: dict[str, Any]) -> dict[str, Any]:
    return {**dict(value or {}), "purged_mock_runtime": True, "purge_reason": PURGE_REASON}

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.main import create_app
from app.db.models import PublishTimingSuggestion
from app.services.m11_1 import (
    PublishTimingPolicyService,
    _launch_policy_source,
    _suggest_time,
    publish_timing_suggestion_read,
)


def _policy() -> SimpleNamespace:
    timestamp = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        channel_workspace_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        policy_version=3,
        state="APPROVED",
        timezone="America/New_York",
        publish_weekdays=["MONDAY"],
        publish_local_time="10:00",
        canonical_hash="a" * 64,
        approved_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )


class _ReadOnlySession:
    def __init__(self, policy: SimpleNamespace):
        self.policy = policy
        self.channel = SimpleNamespace(id=policy.channel_workspace_id)
        self.write_attempts: list[str] = []

    def get(self, model, record_id):
        assert record_id == self.policy.channel_workspace_id
        return self.channel

    def scalar(self, statement):
        return self.policy

    def add(self, value):
        self.write_attempts.append("add")

    def flush(self):
        self.write_attempts.append("flush")


def test_publish_timing_policy_get_is_read_only_launch_policy_projection() -> None:
    policy = _policy()
    session = _ReadOnlySession(policy)

    result = PublishTimingPolicyService(session).get(policy.channel_workspace_id)

    assert session.write_attempts == []
    assert result.id == policy.id
    assert result.launch_policy_version_id == policy.id
    assert result.launch_policy_hash == policy.canonical_hash
    assert result.policy_version == 3
    assert result.authority == "FIRST_CHANNEL_LAUNCH_POLICY_VERSION"
    assert result.state == "APPROVED"
    assert result.read_only is True
    assert result.primary_timezone == policy.timezone
    assert result.publish_days == policy.publish_weekdays
    assert (
        result.technical_appendix["legacy_channel_publish_timing_policy_ignored"]
        is True
    )
    assert not hasattr(PublishTimingPolicyService, "update")


def test_publish_timing_route_has_no_mutable_write_operation() -> None:
    application = create_app()
    methods = {
        method
        for route in application.routes
        if getattr(route, "path", None)
        == "/channels/{channel_id}/publish-timing-policy"
        for method in (getattr(route, "methods", set()) or set())
    }

    assert methods == {"GET"}


def test_publish_timing_source_constraint_allows_exact_policy_ref_and_legacy() -> None:
    constraint = next(
        item
        for item in PublishTimingSuggestion.__table__.constraints
        if item.name is not None
        and item.name.startswith("ck_publish_timing_suggestions_")
        and "source" in str(getattr(item, "sqltext", ""))
    )
    expression = str(constraint.sqltext)

    assert "CHANNEL_CONFIG" in expression
    assert "HUMAN_OVERRIDE" in expression
    assert "ANALYTICS_OBSERVED_LATER" in expression
    assert "source ~ '^LP:" in expression


def test_publish_timing_suggestion_carries_exact_policy_lineage() -> None:
    policy = _policy()
    created_at = datetime(2026, 7, 27, 13, 0, tzinfo=UTC)
    source = _launch_policy_source(policy.id)
    assert len(source) == 39
    suggestion = SimpleNamespace(
        id=uuid.uuid4(),
        channel_workspace_id=policy.channel_workspace_id,
        video_project_id=uuid.uuid4(),
        publish_handoff_package_id=uuid.uuid4(),
        target_timezone=policy.timezone,
        operator_timezone=None,
        suggested_publish_at_local=datetime(2026, 7, 27, 10, 0, tzinfo=UTC),
        suggested_publish_at_utc=datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
        operator_local_time=None,
        source=source,
        confidence_label="CONFIGURED",
        operator_summary="Derived from approved launch policy.",
        created_at=created_at,
    )

    result = publish_timing_suggestion_read(
        suggestion,
        launch_policy=policy,
    )

    assert result.source == f"LP:{policy.id}"
    assert result.launch_policy_version_id == policy.id
    assert result.launch_policy_hash == policy.canonical_hash
    assert result.launch_policy_version == policy.policy_version
    assert result.lineage_status == "EXACT"
    assert result.technical_appendix["exact_lineage"] is True


def test_legacy_publish_timing_suggestion_is_historical_read_only() -> None:
    policy = _policy()
    suggestion = SimpleNamespace(
        id=uuid.uuid4(),
        channel_workspace_id=policy.channel_workspace_id,
        video_project_id=None,
        publish_handoff_package_id=None,
        target_timezone="UTC",
        operator_timezone=None,
        suggested_publish_at_local=datetime(2026, 7, 27, 10, tzinfo=UTC),
        suggested_publish_at_utc=datetime(2026, 7, 27, 10, tzinfo=UTC),
        operator_local_time=None,
        source="CHANNEL_CONFIG",
        confidence_label="UNKNOWN",
        operator_summary="Legacy timing suggestion.",
        created_at=datetime(2026, 7, 27, 9, tzinfo=UTC),
    )

    result = publish_timing_suggestion_read(suggestion)

    assert result.lineage_status == "LEGACY_UNKNOWN"
    assert result.launch_policy_version_id is None
    assert result.technical_appendix["legacy_historical_read_only"] is True


def test_suggest_time_uses_approved_policy_timezone_weekday_and_time() -> None:
    policy = _policy()

    local_time, utc_time = _suggest_time(
        policy,
        now=datetime(2026, 7, 27, 13, 0, tzinfo=UTC),
    )

    assert local_time.isoformat() == "2026-07-27T10:00:00-04:00"
    assert utc_time.isoformat() == "2026-07-27T14:00:00+00:00"

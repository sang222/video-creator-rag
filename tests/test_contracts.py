import uuid

import pytest
from pydantic import ValidationError

from app.contracts import AuditEnvelope, EventEnvelope, GateResult


def test_gate_result_without_reason_codes_fails() -> None:
    with pytest.raises(ValidationError):
        GateResult(
            gate_key="m0.health",
            gate_version="1.0.0",
            decision="PASS",
            reason_codes=[],
            summary="empty reasons should fail",
            correlation_id="corr-1",
        )


def test_valid_gate_result_passes() -> None:
    result = GateResult(
        gate_key="m0.health",
        gate_version="1.0.0",
        decision="PASS",
        reason_codes=["SYSTEM_OK"],
        summary="ok",
        correlation_id="corr-1",
    )
    assert result.reason_codes == ["SYSTEM_OK"]


def test_event_envelope_validates() -> None:
    aggregate_id = uuid.uuid4()
    envelope = EventEnvelope(
        event_type="domain.event_recorded",
        event_version=1,
        aggregate_type="company",
        aggregate_id=aggregate_id,
        payload={"ok": True},
        correlation_id="corr-1",
    )
    assert envelope.aggregate_id == aggregate_id


def test_audit_envelope_validates() -> None:
    envelope = AuditEnvelope(
        actor_type="system",
        action="audit.event_recorded",
        target_type="system",
        reason_code="AUDIT_EVENT_RECORDED",
        correlation_id="corr-1",
    )
    assert envelope.action == "audit.event_recorded"

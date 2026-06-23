import uuid

from sqlalchemy import select

from app.contracts import AuditEnvelope, EventEnvelope
from app.db.models import Company, LLMRunSnapshot, User
from app.services import AuditService, ConfigRegistryService, DomainEventBus, RBACService


def test_audit_event_append_read_works(db_session) -> None:
    service = AuditService(db_session)
    event = service.append(
        AuditEnvelope(
            actor_type="system",
            action="audit.event_recorded",
            target_type="system",
            reason_code="AUDIT_EVENT_RECORDED",
            correlation_id="corr-audit",
            payload={"x": 1},
        )
    )
    db_session.commit()
    found = service.get_by_id(event.id)
    assert found is not None
    assert found.payload == {"x": 1}


def test_audit_tail_works(db_session) -> None:
    service = AuditService(db_session)
    first = service.append(
        AuditEnvelope(
            actor_type="system",
            action="audit.first",
            target_type="system",
            reason_code="AUDIT_EVENT_RECORDED",
            correlation_id="corr-tail-1",
        )
    )
    second = service.append(
        AuditEnvelope(
            actor_type="system",
            action="audit.second",
            target_type="system",
            reason_code="AUDIT_EVENT_RECORDED",
            correlation_id="corr-tail-2",
        )
    )
    db_session.commit()
    events = service.tail(limit=1)
    assert len(events) == 1
    assert events[0].id in {first.id, second.id}


def test_domain_event_append_read_works(db_session) -> None:
    bus = DomainEventBus(db_session)
    envelope = EventEnvelope(
        event_type="domain.event_recorded",
        event_version=1,
        aggregate_type="company",
        aggregate_id=uuid.uuid4(),
        payload={"hello": "world"},
        correlation_id="corr-domain",
    )
    event = bus.append(envelope)
    db_session.commit()
    found = bus.get_by_id(event.id)
    assert found is not None
    assert found.payload == {"hello": "world"}


def test_domain_event_mark_published_works(db_session) -> None:
    bus = DomainEventBus(db_session)
    event = bus.append(
        EventEnvelope(
            event_type="domain.event_recorded",
            event_version=1,
            aggregate_type="company",
            aggregate_id=uuid.uuid4(),
            correlation_id="corr-publish",
        )
    )
    assert bus.list_unpublished(limit=10)[0].id == event.id
    bus.mark_published(event.id)
    db_session.commit()
    assert bus.list_unpublished(limit=10) == []


def test_llm_run_snapshot_inert_record_can_be_created(db_session) -> None:
    snapshot = LLMRunSnapshot(
        run_type="contract_test",
        input_payload={"prompt": "not sent"},
        input_hash="hash-in",
        status="created",
        correlation_id="corr-llm",
    )
    db_session.add(snapshot)
    db_session.commit()
    found = db_session.scalar(select(LLMRunSnapshot).where(LLMRunSnapshot.id == snapshot.id))
    assert found is not None
    assert found.provider is None


def test_rbac_role_assignment_minimal_works(db_session) -> None:
    ConfigRegistryService(db_session).seed()
    company = Company(name="Acme", status="active", default_currency="USD")
    user = User(email="ops@example.com", display_name="Ops", status="active")
    db_session.add_all([company, user])
    db_session.flush()
    rbac = RBACService(db_session)
    rbac.assign_role(user_id=user.id, role_key="operator", company_id=company.id)
    db_session.commit()
    assert rbac.user_has_role(user_id=user.id, role_key="operator", company_id=company.id)
    assert rbac.user_has_permission(user_id=user.id, permission="audit:read", company_id=company.id)
    assert not rbac.user_has_permission(user_id=user.id, permission="rbac:assign", company_id=company.id)

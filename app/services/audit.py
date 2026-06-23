import uuid

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.contracts.events import AuditEnvelope
from app.db.models import AuditEvent


class AuditService:
    def __init__(self, session: Session):
        self.session = session

    def append(
        self,
        envelope: AuditEnvelope,
        *,
        company_id: uuid.UUID | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=uuid.uuid4(),
            event_type=envelope.action,
            actor_type=envelope.actor_type,
            actor_id=envelope.actor_id,
            target_type=envelope.target_type,
            target_id=envelope.target_id,
            company_id=company_id,
            correlation_id=envelope.correlation_id,
            reason_code=envelope.reason_code,
            payload=envelope.payload,
            occurred_at=envelope.occurred_at,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def get_by_id(self, event_id: uuid.UUID) -> AuditEvent | None:
        return self.session.get(AuditEvent, event_id)

    def tail(
        self,
        limit: int = 50,
        *,
        company_id: uuid.UUID | None = None,
    ) -> list[AuditEvent]:
        statement: Select[tuple[AuditEvent]] = select(AuditEvent).order_by(
            AuditEvent.created_at.desc()
        )
        if company_id is not None:
            statement = statement.where(AuditEvent.company_id == company_id)
        return list(self.session.scalars(statement.limit(limit)).all())

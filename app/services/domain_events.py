import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.events import EventEnvelope
from app.core.time import utc_now
from app.db.models import DomainEvent


class DomainEventBus:
    def __init__(self, session: Session):
        self.session = session

    def append(
        self,
        envelope: EventEnvelope,
        *,
        company_id: uuid.UUID | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            id=envelope.event_id,
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            aggregate_type=envelope.aggregate_type,
            aggregate_id=envelope.aggregate_id,
            company_id=company_id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            payload=envelope.payload,
            metadata_=envelope.metadata,
            occurred_at=envelope.occurred_at,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def get_by_id(self, event_id: uuid.UUID) -> DomainEvent | None:
        return self.session.get(DomainEvent, event_id)

    def list_unpublished(self, limit: int = 100) -> list[DomainEvent]:
        statement = (
            select(DomainEvent)
            .where(DomainEvent.published_at.is_(None))
            .order_by(DomainEvent.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def mark_published(self, event_id: uuid.UUID) -> DomainEvent:
        event = self.get_by_id(event_id)
        if event is None:
            raise KeyError(f"domain event not found: {event_id}")
        event.published_at = utc_now()
        self.session.flush()
        return event

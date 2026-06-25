from __future__ import annotations

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.contracts import ProviderRegistryEntryCreate
from app.db.models import AuditEvent, DomainEvent, ProviderRegistryEntry, VideoProject
from app.services import ProviderRegistryService

from .helpers.qualification_asserts import EXPECTED_ALEMBIC_HEAD, EXPECTED_M0_M6_TABLES


def test_m0_to_m6_schema_head_tables_and_json_defaults(engine, qualification_factory) -> None:
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_M0_M6_TABLES <= tables
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == EXPECTED_ALEMBIC_HEAD

    flow = qualification_factory.m2_project()
    assert flow.project.financial_summary == {}
    assert flow.project.brand_safety_summary == {}
    assert flow.version.external_entity_refs == [{"type": "brand", "id": "brand-1"}]
    assert flow.version.evidence_refs == [{"type": "manual", "id": "ev-1"}]
    assert flow.version.context_refs == [{"type": "context_pack_snapshot", "id": "ctx-1"}]
    assert flow.version.claim_refs == [{"type": "claim", "id": "cl-1"}]


def test_fk_unique_constraints_and_failed_transaction_rollback(db_session, qualification_factory) -> None:
    qualification_factory.seed_all()
    db_session.commit()
    before = (
        db_session.query(AuditEvent).count(),
        db_session.query(DomainEvent).count(),
        db_session.query(ProviderRegistryEntry).count(),
    )
    try:
        ProviderRegistryService(db_session).create_entry(
            data=ProviderRegistryEntryCreate(provider_key="mock_llm", provider_name="Duplicate", provider_type="LLM")
        )
    except Exception:
        db_session.rollback()
    after = (
        db_session.query(AuditEvent).count(),
        db_session.query(DomainEvent).count(),
        db_session.query(ProviderRegistryEntry).count(),
    )
    assert after == before

    db_session.add(VideoProject(title="bad", company_id=None, channel_workspace_id=None, policy_snapshot_id=None, created_by_user_id=None))
    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()
    else:  # pragma: no cover
        raise AssertionError("invalid project insert unexpectedly succeeded")

    assert db_session.scalars(select(VideoProject)).all() == []

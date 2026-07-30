from __future__ import annotations

import hashlib
import runpy
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.contracts.production_publish import FinalVideoDecisionCreate
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailureError
from app.services.operator_cockpit import OperatorCockpitService
from app.services.production_publish import ProductionPublishService
from app.services.production_workflow import ProductionWorkflowCoordinator


ROOT = Path(__file__).resolve().parents[1]
_PHASE5 = runpy.run_path(str(ROOT / "tests/test_phase5_final_publish.py"))
_ready_final = _PHASE5["_ready_final"]
_actor = _PHASE5["_actor"]


def test_operator_resources_are_company_scoped(db_session: Session) -> None:
    first = _ready_final(db_session)
    second = _ready_final(db_session)
    assert second.candidate is not None
    first_actor = _actor(first.scope)

    with pytest.raises(ForbiddenError, match="review.final_decide"):
        ProductionPublishService(db_session).decide(
            candidate_id=second.candidate.id,
            data=FinalVideoDecisionCreate(
                command_id=uuid.uuid4(),
                decision="UPLOAD",
            ),
            actor=first_actor,
        )

    with pytest.raises(ForbiddenError, match="production.read"):
        ProductionWorkflowCoordinator(db_session).list(
            company_id=second.scope.company.id,
            actor=first_actor,
            view="all",
        )

    with pytest.raises(NotFoundError, match="video project not found"):
        OperatorCockpitService(db_session).build(
            actor=first_actor,
            project_id=second.scope.project.id,
        )


def test_verified_local_media_resolver_rejects_tamper_and_symlink(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"\x00\x00\x00\x18ftypisom-vcos-phase6-media"
    ready = _ready_final(
        db_session,
        local_archive_root=tmp_path,
        local_archive_payload=payload,
    )
    assert ready.candidate is not None
    candidate = ready.candidate
    actor = _actor(ready.scope)
    checksum = hashlib.sha256(payload).hexdigest()
    archive_dir = tmp_path / "archive" / str(candidate.video_project_id)
    video_path = archive_dir / f"{checksum}.mp4"
    monkeypatch.setenv("VCOS_V2_PRODUCTION_ROOT", str(tmp_path))

    service = ProductionPublishService(db_session)
    resolved = service.resolve_verified_candidate_media(
        candidate_id=candidate.id,
        actor=actor,
    )
    assert resolved.path == video_path
    assert resolved.checksum_sha256 == checksum

    video_path.write_bytes(payload + b"-tampered")
    with pytest.raises(
        ValidationFailureError,
        match="READBACK_CHECKSUM_MISMATCH",
    ):
        service.resolve_verified_candidate_media(
            candidate_id=candidate.id,
            actor=actor,
        )

    video_path.write_bytes(payload)
    real_path = archive_dir / "real.mp4"
    video_path.rename(real_path)
    video_path.symlink_to(real_path)
    with pytest.raises(ValidationFailureError, match="SYMLINK_REJECTED"):
        service.resolve_verified_candidate_media(
            candidate_id=candidate.id,
            actor=actor,
        )


def test_local_archive_reference_cannot_smuggle_a_path(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_final(db_session)
    assert ready.candidate is not None
    ready.candidate.archive_object_ref = (
        f"vcos-local-archive://{ready.scope.project.id}/{'a' * 64}/../secrets.mp4"
    )
    monkeypatch.setenv("VCOS_V2_PRODUCTION_ROOT", str(tmp_path))

    with pytest.raises(
        ValidationFailureError,
        match="AUTHORITY_MISMATCH",
    ):
        ProductionPublishService(db_session).resolve_verified_candidate_media(
            candidate_id=ready.candidate.id,
            actor=_actor(ready.scope),
        )

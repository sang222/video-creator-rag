from __future__ import annotations

import hashlib

import pytest

from app.contracts.long_production import FinalMediaCloseoutRequest, ReviewMediaCandidate
from app.core.errors import ValidationFailureError
from app.services.long_production import FinalMediaCloseoutService
from app.services.native_render_plan import stable_hash


def _candidate(path, *, production_eligible: bool = True) -> ReviewMediaCandidate:
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "candidate_id": "review-candidate://fixture",
        "project_ref": "video-project://fixture",
        "package_ref": "scripted-package://fixture",
        "plan_ref": "native-render-plan://fixture",
        "output_file_ref": str(path),
        "output_sha256": checksum,
        "technical_media_qc_ref": "technical-qc://fixture/PASS",
        "technical_media_qc_hash": "technical-qc-hash",
        "creative_media_qc_ref": "creative-qc://fixture/ACCEPTED",
        "creative_media_qc_hash": "creative-qc-hash",
        "production_eligible": production_eligible,
        "not_publishable": not production_eligible,
        "human_review_status": "PASS",
    }
    return ReviewMediaCandidate(**payload, content_hash=stable_hash(payload))


def _request(candidate, **changes) -> FinalMediaCloseoutRequest:
    values = {
        "production_eligible": True,
        "review_candidate": candidate,
        "human_review_decision": "PASS",
        "reviewed_hash": candidate.output_sha256,
        "human_review_receipt_ref": "human-review://fixture/PASS",
        "technical_qc_result": "PASS",
        "creative_review_result": "ACCEPTED",
        "archive_required": True,
        "archive_verification_result": "PASS",
        "package_lineage_valid": True,
        "legacy_incomplete_package": False,
        "provenance_complete": True,
        "rights_disclosure_resolved": True,
        "file_ref": candidate.output_file_ref,
        "file_checksum": candidate.output_sha256,
    }
    values.update(changes)
    return FinalMediaCloseoutRequest(**values)


def test_final_media_closeout_blocks_every_premature_boundary(tmp_path) -> None:
    media = tmp_path / "candidate.mp4"
    media.write_bytes(b"fixture-final-media-bytes")
    candidate = _candidate(media)
    negative = (
        ({"human_review_decision": "PENDING", "human_review_receipt_ref": None}, "HUMAN_REVIEW_PASS_REQUIRED"),
        ({"reviewed_hash": "wrong-hash"}, "REVIEWED_HASH_MISMATCH"),
        ({"archive_verification_result": "PENDING"}, "ARCHIVE_VERIFICATION_REQUIRED"),
        ({"technical_qc_result": "FAIL"}, "TECHNICAL_QC_PASS_REQUIRED"),
        ({"legacy_incomplete_package": True}, "STRICT_PACKAGE_LINEAGE_REQUIRED"),
        ({"file_ref": None, "file_checksum": None}, "FILE_REF_CHECKSUM_REQUIRED"),
    )
    for changes, reason in negative:
        with pytest.raises(ValidationFailureError, match=reason):
            FinalMediaCloseoutService.validate(_request(candidate, **changes))


def test_final_media_closeout_accepts_only_complete_exact_candidate(tmp_path) -> None:
    media = tmp_path / "candidate.mp4"
    media.write_bytes(b"fixture-final-media-bytes")
    candidate = _candidate(media)
    assert FinalMediaCloseoutService.validate(_request(candidate)) == {
        "result": "PASS",
        "eligible_for_final_media_registration": True,
        "file_ref": str(media),
        "file_checksum": candidate.output_sha256,
        "review_candidate_ref": candidate.candidate_id,
    }
    fixture_candidate = _candidate(media, production_eligible=False)
    with pytest.raises(ValidationFailureError, match="PRODUCTION_ELIGIBILITY_REQUIRED"):
        FinalMediaCloseoutService.validate(_request(fixture_candidate))

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Reuse the self-contained finalizer if a queued writer has not committed it.
v4 = ROOT / "tools/finalize_remaining_debt_v4.py"
if v4.exists():
    subprocess.run([sys.executable, str(v4)], cwd=ROOT, check=True)

service = ROOT / "app/services/remaining_debt_closeout.py"
youtube = ROOT / "app/services/youtube_delivery.py"
tests = ROOT / "tests/test_remaining_debt_closeout.py"

service_text = service.read_text(encoding="utf-8")
helper_marker = "def require_canonical_series_delivery_projection("
if helper_marker not in service_text:
    anchor = "\n\n@dataclass(frozen=True, slots=True)\nclass ArchitectureAuditResult:"
    position = service_text.find(anchor)
    if position < 0:
        raise RuntimeError("series delivery guard insertion anchor not found")
    helper = '''

def require_canonical_series_delivery_projection(
    session: Session,
    *,
    series_plan_id: uuid.UUID,
    publication_receipt_id: uuid.UUID,
    proposed_public_ordinal: int | None,
    proposed_playlist_position: int | None,
) -> SeriesPublicOrdinal:
    """Require platform delivery to project the D15 canonical public ordinal."""

    row = session.scalar(
        select(SeriesPublicOrdinal).where(
            SeriesPublicOrdinal.series_plan_id == series_plan_id,
            SeriesPublicOrdinal.publication_receipt_id == publication_receipt_id,
        )
    )
    if row is None:
        raise ValidationFailureError("SERIES_CANONICAL_PUBLIC_ORDINAL_REQUIRED")
    if proposed_public_ordinal is not None and int(proposed_public_ordinal) != row.public_ordinal:
        raise ValidationFailureError("SERIES_PUBLIC_ORDINAL_AUTHORITY_MISMATCH")
    if (
        proposed_playlist_position is not None
        and int(proposed_playlist_position) != row.playlist_position
    ):
        raise ValidationFailureError("SERIES_PLAYLIST_POSITION_AUTHORITY_MISMATCH")
    return row
'''
    service.write_text(
        service_text[:position] + helper + service_text[position:],
        encoding="utf-8",
    )

# Guard the existing PR #3 platform projection without replacing its lineage,
# readback, or immutable receipt logic.
youtube_text = youtube.read_text(encoding="utf-8")
wrapper_marker = "_VCOS_D15_SERIES_GUARD_INSTALLED"
if wrapper_marker not in youtube_text:
    wrapper = '''

# D15 canonical public ordinal guard.  The existing YouTube episode binding is
# a destination projection, never a second editorial authority.
_VCOS_D15_SERIES_GUARD_INSTALLED = True
_original_bind_public_episode_ordinal = YouTubeDeliveryService.bind_public_episode_ordinal


def _bind_public_episode_ordinal_from_canonical_authority(
    self: YouTubeDeliveryService,
    *,
    episode_binding_id: uuid.UUID,
    data: YouTubeSeriesOrdinalBind,
):
    from app.services.remaining_debt_closeout import (
        require_canonical_series_delivery_projection,
    )

    binding = self.session.get(YouTubeSeriesEpisodeBinding, episode_binding_id)
    if binding is None:
        raise NotFoundError(
            f"youtube series episode binding not found: {episode_binding_id}"
        )
    receipt_id = getattr(binding, "public_publication_receipt_id", None)
    series_plan_id = getattr(binding, "series_plan_id", None)
    if receipt_id is None or series_plan_id is None:
        raise ValidationFailureError("SERIES_PUBLICATION_RECEIPT_AUTHORITY_REQUIRED")
    proposed_public_ordinal = None
    proposed_playlist_position = None
    for attribute in ("public_ordinal", "public_episode_ordinal"):
        value = getattr(data, attribute, None)
        if value is not None:
            proposed_public_ordinal = int(value)
            break
    for attribute in ("playlist_position", "playlist_order"):
        value = getattr(data, attribute, None)
        if value is not None:
            proposed_playlist_position = int(value)
            break
    require_canonical_series_delivery_projection(
        self.session,
        series_plan_id=series_plan_id,
        publication_receipt_id=receipt_id,
        proposed_public_ordinal=proposed_public_ordinal,
        proposed_playlist_position=proposed_playlist_position,
    )
    return _original_bind_public_episode_ordinal(
        self,
        episode_binding_id=episode_binding_id,
        data=data,
    )


YouTubeDeliveryService.bind_public_episode_ordinal = (
    _bind_public_episode_ordinal_from_canonical_authority
)
'''
    youtube.write_text(youtube_text.rstrip() + wrapper + "\n", encoding="utf-8")

# Focused proof that delivery cannot invent or reorder a public episode number.
test_text = tests.read_text(encoding="utf-8")
if "test_series_delivery_requires_canonical_public_ordinal" not in test_text:
    test_text = test_text.replace(
        "    SeriesAuthorityService,\n)",
        "    SeriesAuthorityService,\n    require_canonical_series_delivery_projection,\n)",
        1,
    )
    test_text += r'''


def test_series_delivery_requires_canonical_public_ordinal(db: Session) -> None:
    company_id, channel_id, series_plan_id = _scope()
    service = SeriesAuthorityService(db)
    arc = service.create_arc(
        company_id=company_id,
        channel_workspace_id=channel_id,
        series_plan_id=series_plan_id,
        arc_mode="ROLLING",
        planned_episode_count=None,
        premise="Canonical delivery projection",
        coverage_policy={},
    )
    service.activate_arc(
        arc_id=arc.id,
        actor_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        reason="Activate",
    )
    receipt_id = uuid.uuid4()
    ordinal = service.record_publication(
        series_plan_id=series_plan_id,
        publication_receipt_id=receipt_id,
        video_project_id=uuid.uuid4(),
        technical_attempt_ref="attempt-777",
        published_at=utc_now(),
    )
    projected = require_canonical_series_delivery_projection(
        db,
        series_plan_id=series_plan_id,
        publication_receipt_id=receipt_id,
        proposed_public_ordinal=1,
        proposed_playlist_position=0,
    )
    assert projected.id == ordinal.id
    with pytest.raises(
        ValidationFailureError, match="SERIES_PUBLIC_ORDINAL_AUTHORITY_MISMATCH"
    ):
        require_canonical_series_delivery_projection(
            db,
            series_plan_id=series_plan_id,
            publication_receipt_id=receipt_id,
            proposed_public_ordinal=2,
            proposed_playlist_position=0,
        )
'''
    tests.write_text(test_text, encoding="utf-8")

subprocess.run(
    [
        "ruff",
        "format",
        "app/services/remaining_debt_closeout.py",
        "app/services/youtube_delivery.py",
        "tests/test_remaining_debt_closeout.py",
    ],
    cwd=ROOT,
    check=True,
)

for relative in (
    ".github/workflows/debt-closeout-source.yml",
    ".github/workflows/apply-remaining-debt-closeout.yml",
    ".github/workflows/fix-remaining-debt-closeout.yml",
    ".github/workflows/complete-remaining-debt-closeout.yml",
    ".github/workflows/final-hardening-v2.yml",
    ".github/workflows/finalize-remaining-debt-v3.yml",
    ".github/workflows/finalize-remaining-debt-v4.yml",
    ".github/workflows/finalize-remaining-debt-v5.yml",
    "tools/finalize_remaining_debt_closeout.py",
    "tools/harden_remaining_debt_closeout.py",
    "tools/complete_remaining_debt_closeout.py",
    "tools/final_hardening_v2.py",
    "tools/finalize_remaining_debt_v3.py",
    "tools/finalize_remaining_debt_v4.py",
    "tools/finalize_remaining_debt_v5.py",
):
    (ROOT / relative).unlink(missing_ok=True)

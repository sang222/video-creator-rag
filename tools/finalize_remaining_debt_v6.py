from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

v5 = ROOT / "tools/finalize_remaining_debt_v5.py"
if v5.exists():
    subprocess.run([sys.executable, str(v5)], cwd=ROOT, check=True)

youtube = ROOT / "app/services/youtube_delivery.py"
text = youtube.read_text(encoding="utf-8")
start = text.find("# D15 canonical public ordinal guard.")
if start < 0:
    raise RuntimeError("canonical series delivery guard not found")
replacement = '''# D15 canonical public ordinal guard. The existing YouTube episode binding is
# a destination projection, never a second editorial authority. Legacy series
# without any D15 arc retain the pre-0085 behavior until explicit bootstrap.
_VCOS_D15_SERIES_GUARD_INSTALLED = True
_original_bind_public_episode_ordinal = YouTubeDeliveryService.bind_public_episode_ordinal


def _bind_public_episode_ordinal_from_canonical_authority(
    self: YouTubeDeliveryService,
    *,
    episode_binding_id: uuid.UUID,
    data: YouTubeSeriesOrdinalBind,
):
    from app.db.models.remaining_debt import SeriesArcVersion
    from app.services.remaining_debt_closeout import (
        require_canonical_series_delivery_projection,
    )

    binding = self.session.get(YouTubeSeriesEpisodeBinding, episode_binding_id)
    if binding is None:
        raise NotFoundError(
            f"youtube series episode binding not found: {episode_binding_id}"
        )
    series_plan_id = getattr(binding, "series_plan_id", None)
    if series_plan_id is None:
        raise ValidationFailureError("SERIES_PLAN_AUTHORITY_REQUIRED")
    d15_exists = self.session.scalar(
        select(SeriesArcVersion.id).where(
            SeriesArcVersion.series_plan_id == series_plan_id
        )
    )
    if d15_exists is None:
        return _original_bind_public_episode_ordinal(
            self,
            episode_binding_id=episode_binding_id,
            data=data,
        )
    receipt_id = getattr(binding, "public_publication_receipt_id", None)
    if receipt_id is None:
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
youtube.write_text(text[:start].rstrip() + "\n\n" + replacement + "\n", encoding="utf-8")

subprocess.run(
    [
        "ruff",
        "format",
        "app/services/youtube_delivery.py",
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
    ".github/workflows/finalize-remaining-debt-v6.yml",
    "tools/finalize_remaining_debt_closeout.py",
    "tools/harden_remaining_debt_closeout.py",
    "tools/complete_remaining_debt_closeout.py",
    "tools/final_hardening_v2.py",
    "tools/finalize_remaining_debt_v3.py",
    "tools/finalize_remaining_debt_v4.py",
    "tools/finalize_remaining_debt_v5.py",
    "tools/finalize_remaining_debt_v6.py",
):
    (ROOT / relative).unlink(missing_ok=True)

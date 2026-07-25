"""Permit truthful native FFmpeg lineage on production FinalMediaRef rows.

Revision ID: 0039_mr1_final_media
Revises: 0038_lpro1_daily_mode
Create Date: 2026-07-19 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0039_mr1_final_media"
down_revision: str | None = "0038_lpro1_daily_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_HISTORICAL_PROVIDER_TYPES = (
    "'WORKFLOW_ORCHESTRATOR','LLM_SCRIPT_ENGINE','API_NATIVE_TTS',"
    "'CAPTION_TIMELINE_ENGINE','AI_VIDEO_HERO_PROVIDER',"
    "'CLOUD_TEMPLATE_RENDERER_LIGHT','CLOUD_FINAL_ASSEMBLY_RENDERER',"
    "'MEDIA_STORAGE','MEDIA_QC_ENGINE','PUBLISH_PACKAGE_BUILDER',"
    "'API_NATIVE_STOCK_PROVIDER','FREE_FALLBACK_PROVIDER','MOCK_PROVIDER',"
    "'DEFERRED_MANUAL_LIBRARY'"
)
_MR1_PROVIDER_TYPES = _HISTORICAL_PROVIDER_TYPES + ",'LOCAL_RENDERER_CAPABILITY'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_final_media_refs_provider_type",
        "final_media_refs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_final_media_refs_provider_type",
        "final_media_refs",
        f"provider_type is null or provider_type in ({_MR1_PROVIDER_TYPES})",
    )


def downgrade() -> None:
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(
            "select count(*) from final_media_refs "
            "where provider_type = 'LOCAL_RENDERER_CAPABILITY'"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError("DOWNGRADE_BLOCKED_NATIVE_FFMPEG_FINAL_MEDIA_REFS_EXIST")
    op.drop_constraint(
        "ck_final_media_refs_provider_type",
        "final_media_refs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_final_media_refs_provider_type",
        "final_media_refs",
        f"provider_type is null or provider_type in ({_HISTORICAL_PROVIDER_TYPES})",
    )

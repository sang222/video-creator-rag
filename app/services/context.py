from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    ChannelWorkspace,
    Company,
    EditorialPlaybook,
    WorkspaceBudgetPolicy,
    WorkspaceOperationalConstitution,
    WorkspaceProfile,
)
from app.services.skill_pack import SkillPackLoader


MONETIZATION_RULE_1 = (
    "Every agent must prioritize increasing the channel's probability of monetization approval "
    "and sustainable revenue, while preserving platform safety, audience trust, and brand identity."
)


class WorkspaceContextService:
    def get_context(self, db: Session, workspace_id: str) -> dict:
        workspace = db.get(ChannelWorkspace, workspace_id)
        if not workspace:
            raise KeyError(f"workspace not found: {workspace_id}")
        profile = db.get(WorkspaceProfile, workspace_id)
        budget = db.execute(
            select(WorkspaceBudgetPolicy).where(
                WorkspaceBudgetPolicy.company_id == workspace.company_id,
                WorkspaceBudgetPolicy.workspace_id == workspace.id,
            )
        ).scalar_one_or_none()
        playbook = db.execute(
            select(EditorialPlaybook).where(
                EditorialPlaybook.company_id == workspace.company_id,
                EditorialPlaybook.workspace_id == workspace.id,
                EditorialPlaybook.active.is_(True),
            )
        ).scalar_one_or_none()

        return {
            "company_id": workspace.company_id,
            "workspace_id": workspace.id,
            "platform": workspace.platform,
            "channel_name": workspace.channel_name,
            "niche": workspace.niche,
            "language": workspace.language,
            "target_market": workspace.target_market or (profile.target_market if profile else []),
            "maturity_stage": workspace.maturity_stage,
            "follower_count": workspace.follower_count,
            "published_video_count": workspace.published_video_count,
            "baseline_confidence": workspace.baseline_confidence,
            "brand_voice": profile.brand_voice if profile else None,
            "risk_tolerance": (profile.config_json or {}).get("risk_tolerance", "MEDIUM") if profile else "MEDIUM",
            "quality_bar": (profile.config_json or {}).get("quality_bar", "MEDIUM") if profile else "MEDIUM",
            "default_workflow_mode": profile.default_workflow_mode if profile else "MONETIZATION_VALIDATION_MODE",
            "playbook_version": playbook.version if playbook else "seed_playbook_v1",
            "budget": {
                "cost_per_video_target": budget.cost_per_video_target if budget else 1.0,
                "hard_max_per_video": budget.hard_max_per_video if budget else 2.5,
                "daily_budget_limit": budget.daily_budget_limit if budget else 5.0,
            },
        }


class ContextCompilerService:
    def __init__(self, skill_loader: SkillPackLoader | None = None) -> None:
        self.skill_loader = skill_loader or SkillPackLoader()

    def compile(self, db: Session, workspace_id: str) -> WorkspaceOperationalConstitution:
        workspace = db.get(ChannelWorkspace, workspace_id)
        if not workspace:
            raise KeyError(f"workspace not found: {workspace_id}")
        company = db.get(Company, workspace.company_id)
        profile = db.get(WorkspaceProfile, workspace_id)
        budget = db.execute(
            select(WorkspaceBudgetPolicy).where(
                WorkspaceBudgetPolicy.company_id == workspace.company_id,
                WorkspaceBudgetPolicy.workspace_id == workspace.id,
            )
        ).scalar_one_or_none()
        playbook = db.execute(
            select(EditorialPlaybook).where(
                EditorialPlaybook.company_id == workspace.company_id,
                EditorialPlaybook.workspace_id == workspace.id,
                EditorialPlaybook.active.is_(True),
            )
        ).scalar_one_or_none()

        for existing in db.scalars(
            select(WorkspaceOperationalConstitution).where(
                WorkspaceOperationalConstitution.company_id == workspace.company_id,
                WorkspaceOperationalConstitution.workspace_id == workspace.id,
                WorkspaceOperationalConstitution.active.is_(True),
            )
        ):
            existing.active = False
        db.flush()

        company_constitution = self.skill_loader.load("company_monetization_constitution.md")
        default_playbook = self.skill_loader.load("default_workspace_playbook.md")
        playbook_content = playbook.content_json if playbook else {"principles": ["validate revenue path before scaling"]}
        mode_policy = profile.default_workflow_mode if profile else "MONETIZATION_VALIDATION_MODE"
        cost_policy = {
            "cost_per_video_target": budget.cost_per_video_target if budget else 1.0,
            "hard_max_per_video": budget.hard_max_per_video if budget else 2.5,
            "daily_budget_limit": budget.daily_budget_limit if budget else 5.0,
        }
        content = "\n".join(
            [
                "Workspace Operational Constitution",
                f"Company: {company.name if company else workspace.company_id}",
                f"Workspace/channel: {workspace.channel_name}",
                f"Platform: {workspace.platform}",
                f"Niche: {workspace.niche or 'unspecified'}",
                f"Current maturity stage: {workspace.maturity_stage}",
                f"Current cost/mode policy: mode={mode_policy}; budget={cost_policy}",
                "",
                company_constitution,
                "",
                "Decision test: pass monetization sooner, keep monetization longer, improve sustainable revenue, or create valid learning.",
                "",
                "Workspace Profile",
                f"Brand voice: {profile.brand_voice if profile else 'clear, practical, not hype'}",
                f"Target audience: {profile.target_audience if profile else 'unspecified'}",
                f"Forbidden topics: {', '.join(profile.forbidden_topics) if profile and profile.forbidden_topics else 'none specified'}",
                f"Preferred formats: {', '.join(profile.preferred_formats) if profile and profile.preferred_formats else 'reuse-first educational formats'}",
                "",
                "Monetization Thesis",
                f"Monetization thesis: {profile.monetization_thesis_json if profile else {}}",
                "",
                "Compliance Rules",
                f"Platform rules: {profile.platform_rules_json if profile else {}}",
                "",
                "Workspace Playbook",
                default_playbook,
                f"Playbook version: {playbook.version if playbook else 'seed_playbook_v1'}",
                f"Playbook overrides: {playbook_content}",
                "Authority must reject, revise, downgrade, or escalate work that raises reused/spam risk or breaks brand trust.",
            ]
        )
        token_estimate = max(1, len(content.split()))
        constitution = WorkspaceOperationalConstitution(
            company_id=workspace.company_id,
            workspace_id=workspace.id,
            version=(
                "constitution_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_"
                f"{workspace.id[-6:]}_{uuid4().hex[:8]}"
            ),
            content=content,
            source_versions={
                "company": str(company.updated_at) if company else None,
                "workspace": str(workspace.updated_at),
                "profile": str(profile.updated_at) if profile else None,
                "playbook": playbook.version if playbook else "seed_playbook_v1",
            },
            token_estimate=token_estimate,
            active=True,
        )
        db.add(constitution)
        db.commit()
        db.refresh(constitution)
        return constitution

    def get_active_or_compile(self, db: Session, workspace_id: str) -> WorkspaceOperationalConstitution:
        workspace = db.get(ChannelWorkspace, workspace_id)
        if not workspace:
            raise KeyError(f"workspace not found: {workspace_id}")
        constitution = db.execute(
            select(WorkspaceOperationalConstitution).where(
                WorkspaceOperationalConstitution.company_id == workspace.company_id,
                WorkspaceOperationalConstitution.workspace_id == workspace.id,
                WorkspaceOperationalConstitution.active.is_(True),
            ).order_by(WorkspaceOperationalConstitution.created_at.desc())
        ).scalar_one_or_none()
        if constitution:
            return constitution
        return self.compile(db, workspace_id)

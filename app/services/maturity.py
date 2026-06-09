from sqlalchemy.orm import Session

from app.core.enums import MaturityStage
from app.models.entities import ChannelWorkspace, WorkspaceMaturitySnapshot


class WorkspaceMaturityService:
    mature_follower_threshold = 500_000
    mature_video_threshold = 200
    mature_confidence_threshold = 0.85
    scaled_follower_threshold = 100_000
    scaled_confidence_threshold = 0.75

    def classify_workspace(self, workspace: ChannelWorkspace) -> tuple[str, dict]:
        if workspace.published_video_count < 10 or workspace.follower_count < 1000:
            return MaturityStage.NEW_CHANNEL.value, {
                "rule": "published_video_count < 10 or follower_count < 1000",
                "published_video_count": workspace.published_video_count,
                "follower_count": workspace.follower_count,
            }

        if workspace.published_video_count < 30 or workspace.baseline_confidence < 0.5:
            return MaturityStage.EXPLORING_CHANNEL.value, {
                "rule": "published_video_count < 30 or baseline_confidence < 0.5"
            }

        if workspace.follower_count < 100000 or workspace.baseline_confidence < 0.75:
            return MaturityStage.GROWING_CHANNEL.value, {
                "rule": "follower_count < 100000 or baseline_confidence < 0.75"
            }

        if (
            workspace.follower_count >= self.mature_follower_threshold
            and workspace.published_video_count >= self.mature_video_threshold
            and workspace.baseline_confidence >= self.mature_confidence_threshold
        ):
            return MaturityStage.MATURE_BRAND_CHANNEL.value, {
                "rule": "follower_count >= 500000 and published_video_count >= 200 and baseline_confidence >= 0.85"
            }

        if (
            workspace.follower_count >= self.scaled_follower_threshold
            and workspace.baseline_confidence >= self.scaled_confidence_threshold
        ):
            return MaturityStage.SCALED_CHANNEL.value, {
                "rule": "follower_count >= 100000 and baseline_confidence >= 0.75"
            }

        return MaturityStage.GROWING_CHANNEL.value, {"rule": "fallback growing channel"}

    def classify_and_persist(self, db: Session, workspace: ChannelWorkspace) -> WorkspaceMaturitySnapshot:
        stage, reason = self.classify_workspace(workspace)
        workspace.maturity_stage = stage
        snapshot = WorkspaceMaturitySnapshot(
            company_id=workspace.company_id,
            workspace_id=workspace.id,
            maturity_stage=stage,
            follower_count=workspace.follower_count,
            published_video_count=workspace.published_video_count,
            baseline_confidence=workspace.baseline_confidence,
            reason_json=reason,
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        return snapshot

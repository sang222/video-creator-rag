from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.db.models import ChannelWorkspace
from app.db.session import session_scope
from app.services.config_registry import ConfigRegistryService
from app.services.pkg1_market_revision import PKG1MarketRevisionService


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_KEY = "small-team-ai"


def main() -> int:
    required_catalogs = [
        ROOT / "config/artifact_type_registry.yaml",
        ROOT / "config/media_provider_budget_policy_catalog.yaml",
        ROOT / "config/google_gemini_image_model_price_catalog.yaml",
        ROOT / "config/google_veo_model_price_catalog.yaml",
    ]
    with session_scope() as session:
        ConfigRegistryService(session).seed(required_catalogs)

    with session_scope() as session:
        channel = session.scalar(
            select(ChannelWorkspace).where(ChannelWorkspace.key == CHANNEL_KEY)
        )
        if channel is None:
            raise RuntimeError("PKG1_MARKET_REVISION_CHANNEL_NOT_FOUND")

        service = PKG1MarketRevisionService(session)
        entry = service.entry_status(channel.id)
        if entry["status"] != "PASS":
            raise RuntimeError(
                "PKG1_MARKET_REVISION_ENTRY_FAILED:"
                + ",".join(entry["reason_codes"])
            )
        historical_project = entry["historical_project"]
        operator_id = (
            historical_project.owner_user_id
            or historical_project.created_by_user_id
        )
        result = service.build_revision(
            channel_id=channel.id,
            created_by_user_id=operator_id,
        )
        package = result["package"]
        output = {
            "video_project_id": result["video_project_id"],
            "package_artifact_version_id": result[
                "package_artifact_version_id"
            ],
            "package_content_hash": result["package_content_hash"],
            "revision_id": result["revision_id"],
            "revision_version": result["revision_version"],
            "revision_hash": result["revision_hash"],
            "planning_output_set_hash": package["planning_output_set_hash"],
            "human_review_task_ids": result["human_review_task_ids"],
            "human_review_state": result["human_review_state"],
            "final_state": result["final_state"],
            "exact_bindings": package["exact_bindings"],
            "supersedes": package["supersedes"],
            "old_mr1_approval": package["old_mr1_approval"],
            "destination_status": package["destination_status"],
            "publish_blocker": package["publish_blocker"],
            "no_execution_proof": package["no_execution_proof"],
            "artifact_versions": {
                key: {
                    "artifact_id": value["artifact_id"],
                    "artifact_version_id": value["artifact_version_id"],
                    "version_number": value["version_number"],
                    "content_hash": value["content_hash"],
                }
                for key, value in sorted(result["artifacts"].items())
            },
        }
        session.flush()
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.models import ChannelWorkspace, VideoProject  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.services.config_registry import ConfigRegistryService  # noqa: E402
from app.services.pkg1_sc04_revision import (  # noqa: E402
    DIFF_ARTIFACT_TYPE,
    PROJECT_TYPE,
    REVIEW_PACKET_ARTIFACT_TYPE,
    PKG1SC04RevisionService,
)


CHANNEL_KEY = "small-team-ai"
SOURCE_PROJECT_ID = uuid.UUID("2522a8f1-1ea4-4d66-8ea5-411aaa8f152b")
SOURCE_PACKAGE_ARTIFACT_VERSION_ID = uuid.UUID("7de25ac8-46e4-46da-b112-f805f16ebaaa")
SOURCE_PACKAGE_CONTENT_HASH = (
    "200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd"
)
SOURCE_APPROVAL_DECISION_ID = uuid.UUID("ef766b1d-c1a5-43b8-be98-0751bd055653")
SOURCE_HUMAN_RECEIPT_ARTIFACT_VERSION_ID = uuid.UUID(
    "a35c55b8-6887-4e60-a19c-22928205c572"
)
SOURCE_HUMAN_RECEIPT_CONTENT_HASH = (
    "24a2d4c7b0dec7394a8b78ab646f66750fbca35282700d50dcde77bd304c2231"
)
SUMMARY_PATH = ROOT / "reports/pkg1_sc04_visual_revision_summary.json"
REPORT_PATH = ROOT / "reports/pkg1_sc04_visual_revision_report.md"
REPAIR_CYCLES_PATH = ROOT / "reports/pkg1_sc04_visual_revision_repair_cycles.json"


def _required_uuid(name: str) -> uuid.UUID:
    raw = os.getenv(name)
    if not raw:
        raise RuntimeError(f"PKG1_SC04_REQUIRED_ENV_MISSING:{name}")
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise RuntimeError(f"PKG1_SC04_REQUIRED_UUID_INVALID:{name}") from exc


def _required_sha256(name: str) -> str:
    raw = os.getenv(name)
    if not raw:
        raise RuntimeError(f"PKG1_SC04_REQUIRED_ENV_MISSING:{name}")
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise RuntimeError(f"PKG1_SC04_REQUIRED_SHA256_INVALID:{name}")
    return raw


def _report(summary: dict[str, Any]) -> str:
    attempts = summary["attempt_evidence"]["attempts"]
    attempt_rows = "\n".join(
        "| `{operation_key}` | `{artifact_version_id}` | `{content_hash}` | "
        "`{state}` | `{failure}` |".format(**item)
        for item in attempts
    )
    query_rows = "\n".join(
        f"- `{query}`"
        for query in summary["attempt_evidence"]["old_query_family"]["queries"]
    )
    gate_rows = "\n".join(
        f"| `{key}` | `{verdict}` |"
        for key, verdict in sorted(summary["named_gate_verdict_matrix"].items())
    )
    scope = summary["provider_attempt_scope"]
    drive_scope = scope["google_drive_mutation_scope"]
    drive_phases = ", ".join(
        f"{item['phase']} ({item['boundary']}, max={item['max_mutations']})"
        for item in drive_scope["idempotency_phases"]
    )
    cost = summary["cost_difference"]
    rights = summary["rights_provenance_result"]
    old_scene = summary["old_scene_authority"]
    new_meaning = summary["new_scene_meaning"]
    blueprint = summary["native_motion_blueprint"]
    phase_rows = "\n".join(
        "- `{phase}`: {details}".format(
            phase=phase["phase"],
            details=", ".join(phase.get("items") or phase.get("branches") or []),
        )
        for phase in blueprint["phases"]
    )
    return f"""# Báo cáo sửa Visual SC-04 của PKG1

## Kết quả

- Technical revision: `PASS`
- Root cause: `{summary["root_cause"]}`
- Route mới: `{summary["repaired_route"]}`
- Human package review: `{summary["human_review_state"]}`
- MR1 execution: `{summary["mr1_execution"]}`
- Provider call trong lúc dựng revision: `{summary["no_execution_proof"]["provider_calls"]}`

## Nghĩa của scene và route

- Authority cũ: scene `{old_scene["scene_id"]}`, role `{old_scene["source_role"]}`, route `{old_scene["preferred_source_route"]}`, provider `{old_scene["provider"]}`, attempt cap `{old_scene["attempt_cap"]}`.
- Semantic intent cũ: {old_scene["semantic_intent"]}
- Semantic intent sửa: {new_meaning["semantic_intent"]}
- Scene meaning sửa: {new_meaning["scene_meaning"]}
- Editorial intent sửa: {new_meaning["editorial_intent"]}
- Route mới: `{blueprint["route"]}`; mechanism: `{blueprint["native_mechanism"]}`.

Native motion plan:

{phase_rows}

- Exact text authority: `{blueprint["exact_text_authority"]}`; stock layer allowed: `{blueprint["stock_layer_allowed"]}`; provider execution required: `{blueprint["provider_execution_required"]}`.

## Package bất biến

- Project: `{summary["video_project_id"]}` (`{PROJECT_TYPE}`)
- Revision: `{summary["revision_id"]}` / v{summary["revision_version"]}
- Revision hash: `{summary["revision_hash"]}`
- Package version/hash: `{summary["package_artifact_version_id"]}` / `{summary["package_content_hash"]}`
- Human review task: `{summary["human_review_task_ids"][0]}`
- Source package version/hash: `{summary["source_human_authority"]["approved_package"]["artifact_version_id"]}` / `{summary["source_human_authority"]["approved_package"]["content_hash"]}`
- Source approval: `{summary["source_human_authority"]["approval"]["approval_decision_id"]}` (`{summary["source_human_authority"]["approval"]["approval_scope"]}`)
- Source human receipt version/hash: `{summary["source_human_authority"]["human_review_receipt"]["artifact_version_id"]}` / `{summary["source_human_authority"]["human_review_receipt"]["content_hash"]}`

## Authority Geo/Market

- Ads-only overlay: `{summary["effective_ads_only_policy"]["artifact_version_id"]}` / `{summary["effective_ads_only_policy"]["content_hash"]}`
- Geo closeout evidence: `{summary["geo_closeout_evidence"]["artifact_version_id"]}` / `{summary["geo_closeout_evidence"]["content_hash"]}`
- Effective market policy hash: `{summary["effective_market_policy_hash"]}`
- Không sửa snapshot nền mixed/affiliate; mâu thuẫn được công khai và được overlay bất biến thay thế làm effective truth.

## Hai attempt Pexels được giữ nguyên

| Operation | Artifact version | Hash | State | Failure |
|---|---|---|---|---|
{attempt_rows}

Query family cũ được tái dựng xác định từ intent đã bind:

{query_rows}

Candidate ranking/semantic score là `UNAVAILABLE_NOT_PERSISTED`; không bịa số liệu.

## Phạm vi provider/cost/rights

- SC-04 attempt cap: `{scope["sc04_attempt_cap"]["before"]}` → `{scope["sc04_attempt_cap"]["after"]}`.
- Pexels scene count: `{scope["pexels_scene_count"]["before"]}` → `{scope["pexels_scene_count"]["after"]}`; native scene count: `{scope["native_scene_count"]["before"]}` → `{scope["native_scene_count"]["after"]}`.
- Google Drive planned mutations: `{drive_scope["before_planned_requests"]}` → `{drive_scope["after_planned_requests"]}`; exact idempotency phases: {drive_phases}. The supplement does not mutate the canonical review archive.
- Incremental cost: `${cost["incremental_cost_usd"]}`; actual cost: `{cost["actual_cost"]}`; estimated total/hard cap unchanged: `{cost["estimated_cost"]["unchanged"]}` / `{cost["hard_cap"]["unchanged"]}`.
- Rights/provenance: `{rights["verdict"]}`, SC-04 source `{rights["sc04_source"]}`, stock asset required `{rights["stock_asset_required"]}`, provider output claimed `{rights["provider_output_claimed"]}`.

## Gate matrix

| Gate | Verdict |
|---|---|
{gate_rows}

## Diff và ranh giới review

- Changed scenes: `SC-04`; unchanged scenes exact: `{summary["unchanged_scenes_exact"]}`.
- Không tạo attempt thứ ba, không hạ threshold, không reset ledger, không provider substitution, không runtime fallback.
- MR1 vẫn blocked cho đến khi operator review và PASS đúng package/hash này.

Builder không tạo `ApprovalDecision` và không cấp quyền MR1, render, Drive, YouTube hay provider call.
"""


def main() -> int:
    ads_only_overlay_artifact_version_id = _required_uuid(
        "VCOS_ADS_ONLY_OVERLAY_ARTIFACT_VERSION_ID"
    )
    ads_only_overlay_content_hash = _required_sha256(
        "VCOS_ADS_ONLY_OVERLAY_CONTENT_HASH"
    )
    geo_closeout_artifact_version_id = _required_uuid(
        "VCOS_GEO_CLOSEOUT_ARTIFACT_VERSION_ID"
    )
    geo_closeout_content_hash = _required_sha256("VCOS_GEO_CLOSEOUT_CONTENT_HASH")
    with session_scope() as session:
        ConfigRegistryService(session).seed(
            [ROOT / "config/artifact_type_registry.yaml"]
        )

    with session_scope() as session:
        channel = session.scalar(
            select(ChannelWorkspace).where(ChannelWorkspace.key == CHANNEL_KEY)
        )
        if channel is None:
            raise RuntimeError("PKG1_SC04_CHANNEL_NOT_FOUND")
        source = session.get(VideoProject, SOURCE_PROJECT_ID)
        if source is None:
            raise RuntimeError("PKG1_SC04_EXACT_APPROVED_SOURCE_NOT_FOUND")
        actor_id = source.owner_user_id or source.created_by_user_id
        result = PKG1SC04RevisionService(session).build_revision(
            channel_id=channel.id,
            created_by_user_id=actor_id,
            ads_only_overlay_artifact_version_id=(ads_only_overlay_artifact_version_id),
            ads_only_overlay_content_hash=ads_only_overlay_content_hash,
            geo_closeout_artifact_version_id=(geo_closeout_artifact_version_id),
            geo_closeout_content_hash=geo_closeout_content_hash,
            source_project_id=SOURCE_PROJECT_ID,
            source_package_artifact_version_id=(SOURCE_PACKAGE_ARTIFACT_VERSION_ID),
            source_package_content_hash=SOURCE_PACKAGE_CONTENT_HASH,
            source_approval_decision_id=SOURCE_APPROVAL_DECISION_ID,
            source_human_receipt_artifact_version_id=(
                SOURCE_HUMAN_RECEIPT_ARTIFACT_VERSION_ID
            ),
            source_human_receipt_content_hash=(SOURCE_HUMAN_RECEIPT_CONTENT_HASH),
        )
        package = result["package"]
        packet = result["artifacts"][REVIEW_PACKET_ARTIFACT_TYPE]["content"]
        diff = result["artifacts"][DIFF_ARTIFACT_TYPE]["content"]
        summary = {
            "result": "PASS",
            "video_project_id": result["video_project_id"],
            "project_type": result["project_type"],
            "actor_user_id": str(actor_id),
            "revision_id": result["revision_id"],
            "revision_version": result["revision_version"],
            "revision_hash": result["revision_hash"],
            "package_artifact_version_id": result["package_artifact_version_id"],
            "package_content_hash": result["package_content_hash"],
            "source_human_authority": package["source_human_authority"],
            "planning_output_set_hash": package["planning_output_set_hash"],
            "root_cause": package["root_cause"],
            "repaired_scene": package["repaired_scene"],
            "repaired_route": package["repaired_route"],
            "human_review_task_ids": result["human_review_task_ids"],
            "human_review_state": result["human_review_state"],
            "final_state": result["final_state"],
            "mr1_execution": package["MR1_EXECUTION"],
            "proceed_to_mr1": package["PROCEED_TO_MR1"],
            "effective_ads_only_policy": package["effective_monetization_policy"],
            "geo_closeout_evidence": package["geo_market_delivery_closeout_evidence"],
            "effective_market_policy_hash": package["effective_market_policy_hash"],
            "attempt_evidence": package["attempt_evidence"],
            "old_scene_authority": packet["old_scene_authority"],
            "new_scene_meaning": packet["new_scene_meaning"],
            "native_motion_blueprint": packet["native_motion_blueprint"],
            "provider_attempt_scope": packet["provider_attempt_scope"],
            "cost_difference": packet["cost_difference"],
            "rights_provenance_result": packet["rights_provenance_result"],
            "named_gate_verdict_matrix": packet["named_gate_verdict_matrix"],
            "unchanged_scenes_exact": diff["unchanged_scenes_exact"],
            "no_execution_proof": package["no_execution_proof"],
            "artifact_versions": {
                key: {
                    "artifact_version_id": value["artifact_version_id"],
                    "content_hash": value["content_hash"],
                }
                for key, value in sorted(result["artifacts"].items())
            },
        }
        session.flush()

    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT_PATH.write_text(_report(summary), encoding="utf-8")
    REPAIR_CYCLES_PATH.write_text(
        json.dumps(
            {
                "schema_version": "pkg1.sc04-repair-cycles.v1",
                "result": "PASS",
                "cycles": [
                    {
                        "cycle": 1,
                        "classification": "DETERMINISTIC_PACKAGE_REPAIR",
                        "root_cause": summary["root_cause"],
                        "route": summary["repaired_route"],
                        "provider_calls": summary["no_execution_proof"][
                            "provider_calls"
                        ],
                        "outcome": "PASS_HUMAN_REVIEW_PENDING",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

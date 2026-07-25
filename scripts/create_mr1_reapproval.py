from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from app.contracts.mr1 import MR1ReapprovalCommand
from app.db.session import session_scope
from app.services.config_registry import ConfigRegistryService
from app.services.mr1_reapproval import MR1ReapprovalService


ROOT = Path(__file__).resolve().parents[1]
LEGACY_PROJECT_ID = uuid.UUID("2522a8f1-1ea4-4d66-8ea5-411aaa8f152b")
LEGACY_PKG1_APPROVAL_DECISION_ID = uuid.UUID("ef766b1d-c1a5-43b8-be98-0751bd055653")
LEGACY_PKG1_HUMAN_REVIEW_RECEIPT_VERSION_ID = uuid.UUID(
    "a35c55b8-6887-4e60-a19c-22928205c572"
)
LEGACY_CHANNEL_PROFILE_VERSION_ID = uuid.UUID("d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711")
LEGACY_COMPILED_POLICY_SNAPSHOT_ID = uuid.UUID("e6c33d80-f5d8-4f72-9abc-87de3601b89e")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create one fresh exact MR1 reapproval without starting provider or "
            "render execution. Legacy authority must be selected explicitly."
        )
    )
    parser.add_argument(
        "--authority-mode",
        choices=("legacy", "sc04"),
        required=True,
    )
    parser.add_argument("--project-id", type=uuid.UUID)
    parser.add_argument("--pkg1-approval-decision-id", type=uuid.UUID)
    parser.add_argument("--pkg1-human-review-receipt-version-id", type=uuid.UUID)
    parser.add_argument("--channel-profile-version-id", type=uuid.UUID)
    parser.add_argument("--compiled-policy-snapshot-id", type=uuid.UUID)
    parser.add_argument(
        "--summary-output",
        type=Path,
        help=(
            "Atomic exact reapproval summary. SC-04 defaults to the dedicated "
            "revision summary under reports/."
        ),
    )
    return parser.parse_args()


def _authority_from_args(
    args: argparse.Namespace,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    explicit = (
        args.project_id,
        args.pkg1_approval_decision_id,
        args.pkg1_human_review_receipt_version_id,
        args.channel_profile_version_id,
        args.compiled_policy_snapshot_id,
    )
    if args.authority_mode == "legacy":
        if any(value is not None for value in explicit):
            raise SystemExit(
                "legacy authority uses its frozen defaults; do not mix explicit refs"
            )
        return (
            LEGACY_PROJECT_ID,
            LEGACY_PKG1_APPROVAL_DECISION_ID,
            LEGACY_PKG1_HUMAN_REVIEW_RECEIPT_VERSION_ID,
            LEGACY_CHANNEL_PROFILE_VERSION_ID,
            LEGACY_COMPILED_POLICY_SNAPSHOT_ID,
        )
    if any(value is None for value in explicit):
        raise SystemExit(
            "sc04 authority requires project, package approval, human receipt, "
            "profile, and snapshot refs"
        )
    assert all(value is not None for value in explicit)
    if (
        args.project_id == LEGACY_PROJECT_ID
        or args.pkg1_approval_decision_id == LEGACY_PKG1_APPROVAL_DECISION_ID
        or args.pkg1_human_review_receipt_version_id
        == LEGACY_PKG1_HUMAN_REVIEW_RECEIPT_VERSION_ID
    ):
        raise SystemExit("sc04 authority must not reuse legacy package authority")
    return explicit  # type: ignore[return-value]


def main() -> int:
    args = _parse_args()
    (
        project_id,
        package_approval_id,
        human_receipt_id,
        profile_id,
        snapshot_id,
    ) = _authority_from_args(args)
    with session_scope() as session:
        ConfigRegistryService(session).seed(
            [ROOT / "config/artifact_type_registry.yaml"]
        )

    command = MR1ReapprovalCommand(
        project_id=project_id,
        pkg1_approval_decision_id=package_approval_id,
        pkg1_human_review_receipt_version_id=human_receipt_id,
        channel_profile_version_id=profile_id,
        compiled_policy_snapshot_id=snapshot_id,
    )
    with session_scope() as session:
        result = MR1ReapprovalService(session).approve(command)
        session.flush()
    output_path = args.summary_output
    if args.authority_mode == "sc04" and output_path is None:
        output_path = ROOT / "reports/mr1_reapproval_sc04_revision_summary.json"
    if output_path is not None:
        _write_text_atomic(
            output_path.resolve(),
            json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
        )
    if args.authority_mode == "sc04":
        _write_text_atomic(
            ROOT / "reports/mr1_reapproval_sc04_revision_report.md",
            _sc04_report(result=result, summary_path=output_path),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sc04_report(*, result: dict, summary_path: Path | None) -> str:
    target = result["exact_target"]
    return f"""# MR1 re-approval — PKG1 SC-04 revision

Trạng thái: `MR1_REAPPROVAL_FINAL={result["MR1_REAPPROVAL_FINAL"]}`; MR1 chưa
được chạy và publish vẫn bị khóa.

| Authority | Exact value |
|---|---|
| VideoProject | `{target["project_id"]}` |
| Package ArtifactVersion | `{target["package_artifact_version_id"]}` |
| Package content hash | `{target["package_content_hash"]}` |
| Revision | `{target["revision_id"]}` / `{target["revision_hash"]}` |
| MR1 ApprovalDecision | `{result["approval_decision_id"]}` |
| MR1 approval content hash | `{result["approval_content_hash"]}` |
| Supplemental visual alignment | `{result["exact_bindings"]["supplemental_visual_alignment"]["artifact_version_id"]}` |
| Reuse decision manifest | `{result["reuse_decision_artifact_version_id"]}` / `{result["reuse_decision_content_hash"]}` |
| Summary | `{summary_path}` |

`provider_calls=0`, `render_calls=0`, `drive_calls=0`, `youtube_calls=0` tại
re-approval. Old market/niche visual components chỉ là historical nonvisual
authority; visual authority hiện hành là supplemental alignment exact-hash.
Reuse manifest chạy fail-closed; chỉ output có đủ request/script/provider/settings/
checksum/rights/QC proof mới được phép `REUSE_VALID`.
"""


if __name__ == "__main__":
    raise SystemExit(main())

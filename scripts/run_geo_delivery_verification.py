from __future__ import annotations

# ruff: noqa: E402 -- repository root is inserted before application imports.

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GEO_VERIFICATION_RECEIPT_ARTIFACT_STATUS = "in_review"
GEO_VERIFICATION_RECEIPT_VERSION_STATUS = "submitted"

from app.contracts.geo_delivery import (
    GEO_DELIVERY_ACCEPTANCE_GATES,
    DeliveryVerdict,
    GeoDeliveryVerificationGateResult,
    GeoDeliveryVerificationManifest,
    GeoDeliveryVerificationNodeOutcome,
    GeoDeliveryVerificationReceipt,
    GeoDeliveryVerificationReceiptRunEvidence,
    GeoDeliveryVerificationRun,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.config import get_settings
from app.core.db import reset_db_caches
from app.db.models import ApprovalDecision, Artifact, ArtifactVersion, VideoProject
from app.db.session import session_scope
from app.services.config_registry import ConfigRegistryService
from app.services.geo_delivery_verification import (
    GEO_DELIVERY_PYTEST_RUN_ID,
    GEO_DELIVERY_REQUIRED_RUN_IDS,
    GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS,
    GEO_DELIVERY_REQUIRED_TEST_NODES,
    GEO_DELIVERY_REQUIRED_TEST_TARGETS,
    geo_delivery_workspace_hash,
    validate_geo_delivery_verification_scope,
)
from app.services.workflow import ArtifactService, deterministic_artifact_content_hash


class _ExactPytestOutcomeCollector:
    def __init__(self) -> None:
        self._outcomes: dict[str, str] = {}

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when == "setup" and report.outcome in {"failed", "skipped"}:
            self._outcomes[report.nodeid] = report.outcome
        elif report.when == "call":
            self._outcomes[report.nodeid] = report.outcome
        elif report.when == "teardown" and report.outcome == "failed":
            self._outcomes[report.nodeid] = "failed"

    def exact_outcomes(self) -> list[GeoDeliveryVerificationNodeOutcome]:
        return [
            GeoDeliveryVerificationNodeOutcome(
                node_id=node_id,
                outcome=outcome,
            )
            for node_id, outcome in sorted(self._outcomes.items())
        ]


def _restore_database_runtime(
    *,
    original_database_url: str | None,
    database_url_was_set: bool,
) -> None:
    if database_url_was_set:
        assert original_database_url is not None
        os.environ["VCOS_DATABASE_URL"] = original_database_url
    else:
        os.environ.pop("VCOS_DATABASE_URL", None)
    get_settings.cache_clear()
    reset_db_caches()


def _run_pytest() -> GeoDeliveryVerificationRun:
    collector = _ExactPytestOutcomeCollector()
    pytest_args = ["-q", *GEO_DELIVERY_REQUIRED_TEST_TARGETS]
    output = io.StringIO()
    original_database_url = os.environ.get("VCOS_DATABASE_URL")
    database_url_was_set = "VCOS_DATABASE_URL" in os.environ
    try:
        with redirect_stdout(output), redirect_stderr(output):
            # pytest prepends configured ``addopts`` to the list it receives. Keep the
            # canonical evidence command immutable while allowing pytest to mutate its
            # private execution copy.
            exit_code = int(pytest.main(list(pytest_args), plugins=[collector]))
    finally:
        _restore_database_runtime(
            original_database_url=original_database_url,
            database_url_was_set=database_url_was_set,
        )
    output_bytes = output.getvalue().encode("utf-8")
    outcomes = collector.exact_outcomes()
    counts = {
        name: sum(item.outcome == name for item in outcomes)
        for name in ("passed", "failed", "skipped")
    }
    verdict = (
        DeliveryVerdict.PASS
        if exit_code == 0 and counts["failed"] == 0 and outcomes
        else DeliveryVerdict.BLOCK
    )
    return GeoDeliveryVerificationRun(
        run_id=GEO_DELIVERY_PYTEST_RUN_ID,
        run_kind="PYTEST",
        command=[sys.executable, "-m", "pytest", *pytest_args],
        exit_code=exit_code,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        output_hash=hashlib.sha256(output_bytes).hexdigest(),
        verdict=verdict,
        node_outcomes=outcomes,
    )


def _run_static_check(
    *,
    run_id: str,
    command: list[str],
    expected_output_lines: list[str] | None = None,
    expected_single_line_pattern: str | None = None,
) -> GeoDeliveryVerificationRun:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        exit_code = completed.returncode
        output_bytes = completed.stdout or b""
    except OSError as exc:
        exit_code = 127
        output_bytes = f"{type(exc).__name__}:{exc}".encode("utf-8", errors="replace")
    output_matches = True
    if expected_output_lines is not None:
        actual_lines = [
            line.strip()
            for line in output_bytes.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        output_matches = actual_lines == expected_output_lines
    elif expected_single_line_pattern is not None:
        actual_lines = [
            line.strip()
            for line in output_bytes.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        output_matches = (
            len(actual_lines) == 1
            and re.fullmatch(expected_single_line_pattern, actual_lines[0]) is not None
        )
    passed = exit_code == 0 and output_matches
    return GeoDeliveryVerificationRun(
        run_id=run_id,
        run_kind="STATIC_CHECK",
        command=command,
        exit_code=exit_code,
        passed=int(passed),
        failed=int(not passed),
        skipped=0,
        output_hash=hashlib.sha256(output_bytes).hexdigest(),
        verdict=(DeliveryVerdict.PASS if passed else DeliveryVerdict.BLOCK),
        node_outcomes=[],
    )


def _run_static_checks() -> list[GeoDeliveryVerificationRun]:
    compileall_run_id, alembic_run_id, diff_run_id = (
        GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS
    )
    colocated_alembic = Path(sys.executable).with_name("alembic")
    alembic_executable = (
        str(colocated_alembic) if colocated_alembic.is_file() else "alembic"
    )
    return [
        _run_static_check(
            run_id=compileall_run_id,
            command=[
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "app",
                "scripts",
            ],
        ),
        _run_static_check(
            run_id=alembic_run_id,
            command=[alembic_executable, "heads"],
            expected_single_line_pattern=r"[0-9a-z][0-9a-z_]* \(head\)",
        ),
        _run_static_check(
            run_id=diff_run_id,
            command=["git", "diff", "--check"],
        ),
    ]


def _persist_verification_receipt(
    *,
    manifest: GeoDeliveryVerificationManifest,
) -> ArtifactVersion:
    receipt = GeoDeliveryVerificationReceipt(
        producer="VCOS_MACHINE_VERIFICATION_RUNNER",
        manifest=manifest,
        run_evidence=[
            GeoDeliveryVerificationReceiptRunEvidence(
                run_id=item.run_id,
                run_kind=item.run_kind,
                command=list(item.command),
                exit_code=item.exit_code,
                output_hash=item.output_hash,
                verdict=item.verdict,
            )
            for item in manifest.verification_runs
        ],
    )
    with session_scope() as session:
        ConfigRegistryService(session).seed(
            [ROOT / "config/artifact_type_registry.yaml"]
        )
        source_package = session.get(
            ArtifactVersion, manifest.source_package_artifact_version_id
        )
        source_artifact = (
            session.get(Artifact, source_package.artifact_id)
            if source_package is not None
            else None
        )
        source_project = (
            session.get(VideoProject, source_artifact.video_project_id)
            if source_artifact is not None
            else None
        )
        if (
            source_package is None
            or source_artifact is None
            or source_project is None
            or source_artifact.artifact_type != "package_manifest"
            or source_artifact.current_version_id != source_package.id
            or source_artifact.status != "approved"
            or source_project.status != "approved"
            or source_project.channel_workspace_id != manifest.channel_workspace_id
            or source_project.policy_snapshot_id != manifest.policy_snapshot_id
            or source_package.content_hash != manifest.source_package_content_hash
            or deterministic_artifact_content_hash(source_package.content or {})
            != source_package.content_hash
        ):
            raise RuntimeError("GEO_VERIFICATION_RECEIPT_SOURCE_INVALID")
        approvals = list(
            session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id == source_package.id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
        )
        approvals = [
            item
            for item in approvals
            if (item.metadata_ or {}).get("approval_scope")
            == "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
        ]
        receipt_artifacts = list(
            session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == source_project.id,
                    Artifact.artifact_type
                    == "pkg1_market_revision_human_review_receipt",
                )
            ).all()
        )
        if len(approvals) != 1 or len(receipt_artifacts) != 1:
            raise RuntimeError("GEO_VERIFICATION_RECEIPT_AUTHORITY_MISSING")
        approval = approvals[0]
        approval_metadata = approval.metadata_ or {}
        human_receipt = session.get(
            ArtifactVersion, receipt_artifacts[0].current_version_id
        )
        if (
            human_receipt is None
            or receipt_artifacts[0].status != "approved"
            or human_receipt.status != "approved"
            or human_receipt.created_by_user_id != approval.decided_by_user_id
            or deterministic_artifact_content_hash(human_receipt.content or {})
            != human_receipt.content_hash
            or (human_receipt.content or {}).get("approval_decision_id")
            != str(approval.id)
            or approval_metadata.get("package_artifact_version_id")
            != str(source_package.id)
            or approval_metadata.get("package_content_hash")
            != source_package.content_hash
            or approval_metadata.get("production_package_approved") is not True
        ):
            raise RuntimeError("GEO_VERIFICATION_RECEIPT_AUTHORITY_INVALID")
        receipt_creator_id = approval.decided_by_user_id
        artifact_service = ArtifactService(session)
        artifact = artifact_service.create_artifact(
            data=ArtifactCreate(
                video_project_id=source_project.id,
                artifact_type="geo_delivery_verification_receipt",
                status=GEO_VERIFICATION_RECEIPT_ARTIFACT_STATUS,
                created_by_user_id=receipt_creator_id,
            ),
            correlation_id=f"geo-verification-receipt-{manifest.content_hash}",
        )
        version = artifact_service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=receipt.model_dump(mode="json"),
                status=GEO_VERIFICATION_RECEIPT_VERSION_STATUS,
                created_by_user_id=receipt_creator_id,
                evidence_refs=[
                    {
                        "type": "source_package_manifest",
                        "artifact_version_id": str(source_package.id),
                        "content_hash": source_package.content_hash,
                    }
                ],
                packaging_metadata={
                    "producer": "VCOS_MACHINE_VERIFICATION_RUNNER",
                    "manifest_content_hash": manifest.content_hash,
                    "workspace_hash": manifest.workspace_hash,
                },
            ),
            correlation_id=(
                f"geo-verification-receipt-version-{manifest.content_hash}"
            ),
        )
        return version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run exact Geo closeout verification and write a hash-bound manifest."
        )
    )
    parser.add_argument("--channel-workspace-id", required=True, type=uuid.UUID)
    parser.add_argument("--policy-snapshot-id", required=True, type=uuid.UUID)
    parser.add_argument("--policy-snapshot-hash", required=True)
    parser.add_argument(
        "--source-package-artifact-version-id",
        required=True,
        type=uuid.UUID,
    )
    parser.add_argument("--source-package-content-hash", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/geo_market_delivery_verification_manifest.json",
    )
    parser.add_argument(
        "--receipt-output",
        type=Path,
        default=ROOT / "reports/geo_market_delivery_verification_receipt.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    pytest_run = _run_pytest()
    static_runs = _run_static_checks()
    runs = [pytest_run, *static_runs]
    outcomes = {item.node_id: item.outcome for item in pytest_run.node_outcomes}
    static_checks_passed = all(
        item.verdict == DeliveryVerdict.PASS for item in static_runs
    )
    gates: list[GeoDeliveryVerificationGateResult] = []
    for gate in GEO_DELIVERY_ACCEPTANCE_GATES:
        required_nodes = GEO_DELIVERY_REQUIRED_TEST_NODES[gate]
        required_nodes_passed = all(
            outcomes.get(node_id) == "passed" for node_id in required_nodes
        )
        gate_passed = (
            pytest_run.verdict == DeliveryVerdict.PASS
            and static_checks_passed
            and required_nodes_passed
        )
        gates.append(
            GeoDeliveryVerificationGateResult(
                gate=gate,
                verdict=(
                    DeliveryVerdict.PASS if gate_passed else DeliveryVerdict.BLOCK
                ),
                checks={
                    "pytest_exit_zero": pytest_run.exit_code == 0,
                    "no_failed_nodes": pytest_run.failed == 0,
                    "exact_node_outcomes_recorded": bool(pytest_run.node_outcomes),
                    "all_gate_required_nodes_passed": required_nodes_passed,
                    "compileall_passed": (
                        static_runs[0].verdict == DeliveryVerdict.PASS
                    ),
                    "alembic_single_head": (
                        static_runs[1].verdict == DeliveryVerdict.PASS
                    ),
                    "git_diff_check_passed": (
                        static_runs[2].verdict == DeliveryVerdict.PASS
                    ),
                    "all_run_output_hashes_present": all(
                        bool(item.output_hash) for item in runs
                    ),
                },
                verification_run_ids=list(GEO_DELIVERY_REQUIRED_RUN_IDS),
                required_node_ids=list(required_nodes),
            )
        )
    passed = all(item.verdict == DeliveryVerdict.PASS for item in gates)
    workspace_hash = geo_delivery_workspace_hash(ROOT)
    manifest = GeoDeliveryVerificationManifest(
        producer="VCOS_MACHINE_VERIFICATION_RUNNER",
        generated_at=datetime.now(timezone.utc),
        workspace_hash=workspace_hash,
        repository_revision=f"workspace-sha256:{workspace_hash}",
        channel_workspace_id=args.channel_workspace_id,
        policy_snapshot_id=args.policy_snapshot_id,
        policy_snapshot_hash=args.policy_snapshot_hash,
        source_package_artifact_version_id=(args.source_package_artifact_version_id),
        source_package_content_hash=args.source_package_content_hash,
        verification_runs=runs,
        gate_results=gates,
    )
    validate_geo_delivery_verification_scope(manifest)
    receipt_version = _persist_verification_receipt(
        manifest=manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_output.write_text(
        json.dumps(
            {
                "artifact_version_id": str(receipt_version.id),
                "content_hash": receipt_version.content_hash,
                "manifest_content_hash": manifest.content_hash,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result": "PASS" if passed else "BLOCK",
                "manifest_path": str(args.output),
                "manifest_content_hash": manifest.content_hash,
                "workspace_hash": manifest.workspace_hash,
                "verification_receipt": {
                    "artifact_version_id": str(receipt_version.id),
                    "content_hash": receipt_version.content_hash,
                    "locator_path": str(args.receipt_output),
                },
                "runs": [
                    {
                        "run_id": item.run_id,
                        "run_kind": item.run_kind,
                        "command": item.command,
                        "exit_code": item.exit_code,
                        "passed": item.passed,
                        "failed": item.failed,
                        "skipped": item.skipped,
                        "output_hash": item.output_hash,
                        "exact_node_outcomes": len(item.node_outcomes),
                        "verdict": item.verdict.value,
                    }
                    for item in runs
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

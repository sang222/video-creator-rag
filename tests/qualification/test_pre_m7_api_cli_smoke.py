from __future__ import annotations

import json

from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.main import create_app


runner = CliRunner()


def _json(output: str):
    return json.loads(output)


def test_pre_m7_api_cli_semantic_smoke(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    db_session.commit()
    route_paths = {route.path for route in create_app().routes}
    assert "/health" in route_paths
    assert "/video-projects/{project_id}/readiness" in route_paths
    assert "/production-runs/{run_id}" in route_paths

    for command in [
        ["health"],
        ["config", "seed"],
        ["provider", "seed-mocks"],
        ["gate", "seed-definitions"],
        ["profile", "active", "--channel-id", str(flow.channel.id)],
        ["workflow", "inspect", "--project-id", str(flow.project.id)],
        ["readiness", "inspect", "--project-id", str(flow.project.id)],
        ["daily", "inspect", "--daily-run-id", str(flow.daily_run.id)],
        ["production", "inspect", "--production-run-id", str(flow.production_run.id)],
        ["render-spec", "validate", "--render-spec-snapshot-id", str(flow.production_run.render_spec_snapshot_id)],
        ["captions", "export-srt", "--caption-track-snapshot-id", str(flow.production_run.caption_track_snapshot_id)],
        ["media", "package-inspect", "--render-package-id", str(flow.production_run.render_package_snapshot_id)],
        ["media", "qc-run", "--render-package-id", str(flow.production_run.render_package_snapshot_id)],
    ]:
        result = runner.invoke(cli_app, command)
        assert result.exit_code == 0, result.output

    inspected = runner.invoke(cli_app, ["production", "inspect", "--production-run-id", str(flow.production_run.id)])
    assert _json(inspected.output)["video_project_id"] == str(flow.project.id)

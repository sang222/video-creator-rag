from __future__ import annotations

import os
from decimal import Decimal

import pytest

from app.providers.google_vertex_veo import GoogleVertexVeoExecutionConfig, GoogleVertexVeoProvider, GoogleVertexVeoRequest


def test_real_veo_smoke_skipped_unless_enabled() -> None:
    if os.getenv("VCOS_VEO_REAL_EXECUTION_ENABLED") != "true" or os.getenv("VCOS_VEO_REAL_SMOKE") != "true":
        pytest.skip("real Veo smoke disabled")
    required = {
        "GOOGLE_CLOUD_PROJECT_ID": os.getenv("GOOGLE_CLOUD_PROJECT_ID"),
        "GOOGLE_CLOUD_LOCATION": os.getenv("GOOGLE_CLOUD_LOCATION"),
        "GOOGLE_APPLICATION_CREDENTIALS": os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        "VCOS_VEO_MODEL": os.getenv("VCOS_VEO_MODEL"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        pytest.fail("BLOCKED: missing real Veo smoke env: " + ", ".join(missing))
    result = GoogleVertexVeoProvider().generate_video(
        request=GoogleVertexVeoRequest(
            prompt="A quiet abstract opening hook for a software workflow explainer, no text.",
            model=required["VCOS_VEO_MODEL"] or "veo-3.1-fast",
            mode=os.getenv("VCOS_VEO_MODE", "video-only"),
            resolution=os.getenv("VCOS_VEO_RESOLUTION", "1080p"),
            duration_seconds=Decimal(os.getenv("VCOS_VEO_DEFAULT_DURATION_SECONDS", "8")),
            audio_enabled=os.getenv("VCOS_VEO_AUDIO_ENABLED", "false").lower() == "true",
        ),
        config=GoogleVertexVeoExecutionConfig(
            project_id=required["GOOGLE_CLOUD_PROJECT_ID"],
            location=required["GOOGLE_CLOUD_LOCATION"],
            service_account_path=required["GOOGLE_APPLICATION_CREDENTIALS"],
            real_execution_enabled=True,
            real_smoke_enabled=True,
        ),
    )
    assert result.ok, f"BLOCKED: real Veo smoke failed: {result.error_code}"

from __future__ import annotations

import os
from datetime import date

import pytest

from app.services.m10_3 import YouTubeOwnerAnalyticsProvider, YouTubePublicStatsProvider


def test_real_youtube_public_smoke_skipped_unless_enabled() -> None:
    if os.getenv("VCOS_YOUTUBE_REAL_PUBLIC_SMOKE") != "true":
        pytest.skip("real YouTube public smoke disabled")
    if os.getenv("YOUTUBE_PUBLIC_MONITOR_ENABLED") != "true":
        pytest.fail("BLOCKED: YOUTUBE_PUBLIC_MONITOR_ENABLED is not true")
    api_key = os.getenv("YOUTUBE_DATA_API_KEY")
    video_id = os.getenv("YOUTUBE_TEST_VIDEO_ID")
    if not api_key or not video_id:
        pytest.fail("BLOCKED: YOUTUBE_DATA_API_KEY or YOUTUBE_TEST_VIDEO_ID is missing")
    result = YouTubePublicStatsProvider().fetch(platform_video_id=video_id, api_key=api_key)
    assert result.ok, f"BLOCKED: public YouTube smoke failed: {result.error_code}"


def test_real_youtube_owner_smoke_skipped_unless_enabled() -> None:
    if os.getenv("VCOS_YOUTUBE_REAL_OWNER_SMOKE") != "true":
        pytest.skip("real YouTube owner analytics smoke disabled")
    if os.getenv("YOUTUBE_OWNER_ANALYTICS_ENABLED") != "true":
        pytest.fail("BLOCKED: YOUTUBE_OWNER_ANALYTICS_ENABLED is not true")
    access_token = os.getenv("YOUTUBE_OWNER_ACCESS_TOKEN")
    video_id = os.getenv("YOUTUBE_TEST_VIDEO_ID")
    if not access_token or not video_id:
        pytest.fail("BLOCKED: YOUTUBE_OWNER_ACCESS_TOKEN or YOUTUBE_TEST_VIDEO_ID is missing")
    result = YouTubeOwnerAnalyticsProvider().fetch(
        platform_video_id=video_id,
        access_token=access_token,
        start_date=date.today().replace(day=1),
        end_date=date.today(),
    )
    assert result.ok, f"BLOCKED: owner YouTube smoke failed: {result.error_code}"

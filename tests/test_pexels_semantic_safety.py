from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts.asset_acquisition import AssetRequest
from app.services.mr1_pexels_authority import (
    build_mr1_pexels_asset_request,
    build_mr1_pexels_query_authority,
    mr1_pexels_query_intent_coverage_evidence,
    mr1_pexels_stock_search_intent_coverage_evidence,
)
from app.services.native_render_plan import stable_hash
from app.services.pa1r import (
    GuardedProviderOperation,
    PA1RCallLedger,
    PA1RExecutionGates,
    PexelsPA1RClient,
)
from app.services.provider_asset_manifests import PexelsResponseParser
from app.services.stock_candidate_ranker import StockCandidateRanker


def _request() -> AssetRequest:
    payload = {
        "request_id": "pexels-semantic-safety",
        "scene_id": "SC-SAFETY",
        "source_segment_ids": ["segment-safety"],
        "purpose": "SUPPORT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": (
            "office coworkers review paperwork at conference table"
        ),
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 6,
        "maximum_duration_seconds": 12,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["SUPPORTING_STOCK", "NATIVE_VISUAL"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def _live_video(
    *,
    asset_id: int,
    slug: str,
    media_token: str = "signed-media-secret",
) -> dict[str, Any]:
    return {
        "id": asset_id,
        "width": 1920,
        "height": 1080,
        "duration": 9,
        "url": f"https://www.pexels.com/video/{slug}-{asset_id}/",
        "user": {
            "name": f"Creator {asset_id}",
            "url": f"https://www.pexels.com/@creator-{asset_id}",
        },
        "video_files": [
            {
                "id": asset_id + 1000,
                "width": 1920,
                "height": 1080,
                "file_type": "video/mp4",
                "link": (
                    f"https://videos.pexels.test/{asset_id}.mp4?"
                    f"token={media_token}"
                ),
            }
        ],
    }


class _SearchTransport:
    def __init__(
        self,
        video: dict[str, Any] | list[dict[str, Any]],
    ):
        self.videos = video if isinstance(video, list) else [video]
        self.calls: list[dict[str, Any]] = []

    def json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return {"videos": self.videos}, {"X-Ratelimit-Remaining": "199"}


class _NoDownload:
    def __init__(self) -> None:
        self.calls = 0

    def download(self, **_: Any) -> None:
        self.calls += 1
        raise AssertionError("semantic search tests must not download")


def _passing_gates() -> PA1RExecutionGates:
    return PA1RExecutionGates(
        approval_present=True,
        credential_ready=True,
        billing_quota_ready=True,
        cost_estimate_ready=True,
        idempotency_ready=True,
        paid_attempt_ready=True,
        provider_boundary_ready=True,
        monthly_budget_ready=True,
        global_kill_switch_open=True,
        provider_kill_switch_open=True,
        planned_ledger_exists=True,
    )


def test_live_parser_uses_public_slug_without_fabricating_semantic_metadata():
    video = _live_video(
        asset_id=9001,
        slug="office-coworkers-review-paperwork-at-conference-table",
    )
    video.update(
        {
            "description": "fabricated description must be ignored",
            "tags": ["fabricated", "tags"],
            "composition": "CLEAN",
            "logo_or_text_present": False,
            "identifiable_person_present": False,
            "brand_or_trademark_present": False,
            "motion_suitability": 1.0,
            "channel_identity_fit": 1.0,
            "prior_use_count": 99,
        }
    )

    candidate = PexelsResponseParser().parse({"videos": [video]})[0]

    assert candidate.description == (
        "office coworkers review paperwork at conference table"
    )
    assert candidate.tags == []
    assert candidate.composition == "UNKNOWN"
    assert candidate.logo_or_text_present is None
    assert candidate.identifiable_person_present is None
    assert candidate.brand_or_trademark_present is None
    assert candidate.motion_suitability == 0.5
    assert candidate.channel_identity_fit == 0.5
    assert candidate.prior_use_count == 0


def test_parser_rejects_slug_without_exact_provider_asset_id_binding():
    mismatched = _live_video(
        asset_id=9001,
        slug="office-coworkers-review-paperwork",
    )
    mismatched["url"] = (
        "https://www.pexels.com/video/"
        "office-coworkers-review-paperwork-9999/"
    )
    id_only = _live_video(asset_id=9002, slug="unused")
    id_only["url"] = "https://www.pexels.com/video/9002/"

    assert PexelsResponseParser().parse(
        {"videos": [mismatched, id_only]}
    ) == []


def test_injected_non_official_metadata_cannot_fabricate_semantic_pass():
    video = _live_video(asset_id=9002, slug="sunset-ocean-waves")
    video.update(
        {
            "description": (
                "office coworkers review paperwork at conference table"
            ),
            "tags": [
                "office",
                "coworkers",
                "review",
                "paperwork",
                "conference",
                "table",
            ],
            "composition": "CLEAN",
            "logo_or_text_present": False,
            "identifiable_person_present": False,
            "brand_or_trademark_present": False,
            "motion_suitability": 1.0,
            "channel_identity_fit": 1.0,
        }
    )

    candidate = PexelsResponseParser().parse({"videos": [video]})[0]
    ranking = StockCandidateRanker().rank(
        _request(),
        [candidate],
        minimum_semantic_relevance=0.78,
        confirmed_query_retrieval=True,
    )

    assert candidate.description == "sunset ocean waves"
    assert candidate.tags == []
    assert ranking.selected_candidate_id is None
    assert ranking.candidate_scores[0].dimensions[
        "semantic_relevance"
    ] == 0.25


def test_observable_text_score_can_exceed_old_ceiling_but_weak_match_fails_078():
    parser = PexelsResponseParser()
    strong = parser.parse(
        {
            "videos": [
                _live_video(
                    asset_id=9001,
                    slug=(
                        "office-coworkers-review-paperwork-at-conference-table"
                    ),
                )
            ]
        }
    )[0]
    weak = parser.parse(
        {
            "videos": [
                _live_video(
                    asset_id=9002,
                    slug="office-coworkers-review-generic-meeting",
                )
            ]
        }
    )[0]
    ranker = StockCandidateRanker()

    strong_result = ranker.rank(_request(), [strong])
    weak_result = ranker.rank(_request(), [weak])
    deflated_result = ranker.rank(
        _request(),
        [
            strong.model_copy(
                update={
                    "motion_suitability": 0.0,
                    "channel_identity_fit": 0.0,
                }
            )
        ],
    )
    inflated_result = ranker.rank(
        _request(),
        [
            strong.model_copy(
                update={
                    "motion_suitability": 1.0,
                    "channel_identity_fit": 1.0,
                }
            )
        ],
    )
    strong_semantic = strong_result.candidate_scores[0].dimensions[
        "semantic_relevance"
    ]
    weak_semantic = weak_result.candidate_scores[0].dimensions[
        "semantic_relevance"
    ]

    assert strong_semantic == 1.0
    assert strong_semantic > 0.775
    assert weak_semantic == 0.5
    assert weak_semantic < 0.78
    assert (
        deflated_result.candidate_scores[0].dimensions["semantic_relevance"]
        == inflated_result.candidate_scores[0].dimensions["semantic_relevance"]
        == 1.0
    )
    assert (
        "OBSERVABLE_CANDIDATE_TEXT_SEMANTIC_EVIDENCE"
        in strong_result.ranking_reason_codes
    )


@pytest.mark.parametrize(
    (
        "scene_id",
        "intent",
        "public_title_slug",
        "weak_public_title_slug",
        "expected_query",
    ),
    [
        (
            "SC-07",
            "People discussing office paperwork together.",
            "people-discussing-office-paperwork",
            "people-office-paperwork",
            "people discussing office paperwork workplace b roll",
        ),
        (
            "SC-09",
            "People working together in an office, planning.",
            "people-working-together-at-an-office",
            "people-working-at-an-office",
            "people working together office workplace b roll",
        ),
    ],
)
def test_reviewed_mr1_query_is_feasible_against_public_url_title(
    scene_id,
    intent,
    public_title_slug,
    weak_public_title_slug,
    expected_query,
):
    request = _request().model_copy(
        update={
            "scene_id": scene_id,
            "semantic_visual_intent": intent,
        }
    )
    candidates = PexelsResponseParser().parse(
        {
            "videos": [
                _live_video(
                    asset_id=9001,
                    slug=public_title_slug,
                ),
                _live_video(
                    asset_id=9002,
                    slug=weak_public_title_slug,
                ),
            ]
        }
    )
    authority_payload = {
        "idempotency_key": f"review:{scene_id}",
        "scene_id": scene_id,
        "semantic_intent": intent,
        "minimum_duration_seconds": 8.0,
        "maximum_duration_seconds": 120.0,
    }
    query_authority = build_mr1_pexels_query_authority(
        authority_payload
    )
    query_coverage = mr1_pexels_query_intent_coverage_evidence(
        authority_payload,
        query_authority,
        semantic_fit_threshold=0.78,
    )
    ranking = StockCandidateRanker().rank(
        request,
        candidates,
        minimum_semantic_relevance=0.78,
        confirmed_query_retrieval=True,
    )

    assert query_authority["primary_query"] == expected_query
    assert query_coverage["query_intent_coverage"] == 0.8
    assert query_coverage["intent_token_count"] == 5
    assert query_coverage["required_matched_intent_token_count"] == 4
    assert query_coverage["matched_intent_token_count"] == 4
    assert (
        query_coverage["maximum_missing_intent_token_count_at_threshold"]
        == 1
    )
    assert ranking.selected_candidate_id == "pexels-9001"
    score_by_id = {
        item.candidate_id: item.dimensions["semantic_relevance"]
        for item in ranking.candidate_scores
    }
    assert score_by_id == {
        "pexels-9001": 0.8,
        "pexels-9002": 0.6,
    }


def test_long_intent_whose_query_drops_most_tokens_is_blocked_before_network():
    payload = {
        "idempotency_key": "review:SC-07:unsafe-long-intent",
        "scene_id": "SC-07",
        "semantic_intent": (
            "Office coworkers review paperwork at a small-business conference "
            "table while one person points to a missing field."
        ),
        "minimum_duration_seconds": 8.0,
        "maximum_duration_seconds": 120.0,
    }
    query_authority = build_mr1_pexels_query_authority(payload)

    with pytest.raises(
        ValueError,
        match="^MR1_PEXELS_QUERY_INTENT_COVERAGE_INADEQUATE$",
    ):
        mr1_pexels_query_intent_coverage_evidence(
            payload,
            query_authority,
            semantic_fit_threshold=0.78,
        )


def test_stock_search_subintent_changes_query_without_replacing_package_semantic():
    package_semantic_intent = (
        "A founder explains a detailed filing workflow and its legal context "
        "while a short supporting office subwindow shows observable teamwork."
    )
    payload = {
        "idempotency_key": "review:SC-07:bounded-stock-search",
        "scene_id": "SC-07",
        "semantic_intent": package_semantic_intent,
        "stock_search_intent": (
            "People discussing office paperwork together."
        ),
        "stock_search_intent_scope": (
            "PEXELS_SUPPORTING_SUBWINDOW_CONTEXT_ONLY"
        ),
        "minimum_duration_seconds": 8.0,
        "maximum_duration_seconds": 120.0,
    }
    alternate = {
        **payload,
        "stock_search_intent": (
            "Coworkers organize office documents together."
        ),
    }

    request = build_mr1_pexels_asset_request(payload)
    authority = build_mr1_pexels_query_authority(payload)
    alternate_authority = build_mr1_pexels_query_authority(alternate)
    coverage = mr1_pexels_stock_search_intent_coverage_evidence(
        payload,
        authority,
        semantic_fit_threshold=0.78,
    )

    assert payload["semantic_intent"] == package_semantic_intent
    assert request.semantic_visual_intent == payload[
        "stock_search_intent"
    ]
    assert authority["package_semantic_intent"] == (
        package_semantic_intent
    )
    assert authority["stock_search_intent"] == payload[
        "stock_search_intent"
    ]
    assert authority["intent_field"] == "stock_search_intent"
    assert coverage["package_semantic_intent"] == (
        package_semantic_intent
    )
    assert coverage["stock_search_intent"] == payload[
        "stock_search_intent"
    ]
    assert coverage["result"] == "PASS"
    assert authority["primary_query"] != alternate_authority[
        "primary_query"
    ]
    assert alternate_authority["package_semantic_intent"] == (
        package_semantic_intent
    )


def test_query_client_passes_078_only_from_observable_candidate_text(tmp_path):
    transport = _SearchTransport(
        _live_video(
            asset_id=9001,
            slug="office-coworkers-review-paperwork-at-conference-table",
        )
    )
    downloader = _NoDownload()
    client = PexelsPA1RClient(transport, downloader)

    evidence, selected, _context = client.search_select_once(
        api_key="api-key-must-not-persist",
        request=_request(),
        workspace_directory=tmp_path,
        semantic_fit_threshold=0.78,
    )

    assert selected["provider_asset_id"] == "9001"
    assert evidence["semantic_fit_gate"] == {
        "threshold": 0.78,
        "selected_semantic_relevance": 1.0,
        "result": "PASS",
    }
    scoring = evidence["semantic_scoring_evidence"][0]
    assert scoring["semantic_relevance"] == 1.0
    assert scoring["provider_search_order_score_contribution"] == 0.0
    assert scoring["motion_suitability_score_contribution"] == 0.0
    assert scoring["channel_identity_fit_score_contribution"] == 0.0
    assert client.search_flow_count == 1
    assert downloader.calls == 0
    assert "api-key-must-not-persist" not in json.dumps(evidence)


def test_download_viability_filter_prevents_incompatible_top_candidate_masking(
    tmp_path,
):
    incompatible = _live_video(
        asset_id=9001,
        slug="office-coworkers-review-paperwork-at-conference-table",
    )
    incompatible["video_files"][0]["file_type"] = "video/webm"
    viable = _live_video(
        asset_id=9002,
        slug="office-coworkers-review-paperwork-conference",
    )
    unfiltered_candidates = PexelsResponseParser().parse(
        {"videos": [incompatible, viable]}
    )
    unfiltered_ranking = StockCandidateRanker().rank(
        _request(),
        unfiltered_candidates,
        minimum_semantic_relevance=0.78,
        confirmed_query_retrieval=True,
    )
    assert unfiltered_ranking.selected_candidate_id == "pexels-9001"

    transport = _SearchTransport([incompatible, viable])
    downloader = _NoDownload()
    evidence, selected, _context = PexelsPA1RClient(
        transport,
        downloader,
    ).search_select_once(
        api_key="api-key-must-not-persist",
        request=_request(),
        workspace_directory=tmp_path,
        semantic_fit_threshold=0.78,
    )

    assert selected["provider_asset_id"] == "9002"
    assert evidence["ranking"]["candidate_ids"] == ["pexels-9002"]
    viability = evidence["technical_viability_filter"]
    assert viability["viable_provider_asset_ids"] == ["9002"]
    assert viability["rejected_candidates"] == [
        {
            "candidate_id": "pexels-9001",
            "provider_asset_id": "9001",
            "duration_seconds": 9.0,
            "minimum_duration_met": True,
            "compatible_mp4_rendition_found": False,
            "reason_codes": ["PEXELS_COMPATIBLE_MP4_NOT_FOUND"],
        }
    ]
    assert len(transport.calls) == 1
    assert downloader.calls == 0


def test_download_viability_filter_rejects_short_candidate_before_ranking(
    tmp_path,
):
    too_short = _live_video(
        asset_id=9001,
        slug="office-coworkers-review-paperwork-at-conference-table",
    )
    too_short["duration"] = 5
    viable = _live_video(
        asset_id=9002,
        slug="office-coworkers-review-paperwork-conference",
    )
    transport = _SearchTransport([too_short, viable])
    downloader = _NoDownload()

    evidence, selected, _context = PexelsPA1RClient(
        transport,
        downloader,
    ).search_select_once(
        api_key="api-key-must-not-persist",
        request=_request(),
        workspace_directory=tmp_path,
        semantic_fit_threshold=0.78,
    )

    assert selected["provider_asset_id"] == "9002"
    assert evidence["ranking"]["candidate_ids"] == ["pexels-9002"]
    assert evidence["technical_viability_filter"]["rejected_candidates"] == [
        {
            "candidate_id": "pexels-9001",
            "provider_asset_id": "9001",
            "duration_seconds": 5.0,
            "minimum_duration_met": False,
            "compatible_mp4_rendition_found": True,
            "reason_codes": ["DURATION_BELOW_REQUEST_MINIMUM"],
        }
    ]
    assert len(transport.calls) == 1
    assert downloader.calls == 0


def test_semantic_gate_selects_strong_candidate_even_when_weak_total_is_higher(
):
    weak, strong = PexelsResponseParser().parse(
        {
            "videos": [
                _live_video(
                    asset_id=9002,
                    slug=(
                        "office-coworkers-review-paperwork-generic-meeting"
                    ),
                ),
                _live_video(
                    asset_id=9001,
                    slug=(
                        "office-coworkers-review-paperwork-conference-table"
                    ),
                ),
            ]
        }
    )
    weak = weak.model_copy(
        update={
            "motion_suitability": 1.0,
            "channel_identity_fit": 1.0,
            "composition": "CLEAN",
        }
    )
    strong = strong.model_copy(
        update={
            "motion_suitability": 0.0,
            "channel_identity_fit": 0.0,
            "composition": "UNKNOWN",
        }
    )
    ranking = StockCandidateRanker().rank(
        _request(),
        [weak, strong],
        minimum_semantic_relevance=0.78,
        confirmed_query_retrieval=True,
    )

    assert ranking.candidate_scores[0].candidate_id == "pexels-9002"
    assert ranking.selected_candidate_id == "pexels-9001"
    selected_score = next(
        item
        for item in ranking.candidate_scores
        if item.candidate_id == ranking.selected_candidate_id
    )
    assert selected_score.dimensions["semantic_relevance"] == 1.0


def test_no_download_viable_candidate_persists_sanitized_failure_receipt(
    tmp_path,
):
    api_key = "api-key-must-not-persist"
    media_token = "signed-media-secret-must-not-persist"
    too_short = _live_video(
        asset_id=9001,
        slug="office-coworkers-review-paperwork-at-conference-table",
        media_token=media_token,
    )
    too_short["duration"] = 5
    incompatible = _live_video(
        asset_id=9002,
        slug="office-coworkers-review-paperwork-at-conference-table",
        media_token=media_token,
    )
    incompatible["video_files"][0]["file_type"] = "video/webm"
    transport = _SearchTransport([too_short, incompatible])
    downloader = _NoDownload()
    client = PexelsPA1RClient(transport, downloader)
    ledger = PA1RCallLedger(tmp_path / "ledger.json")
    ledger.plan(
        "pexels",
        provider="pexels_api",
        operation="search",
        paid=False,
        idempotency_key="one-search-no-viable-download",
    )

    with pytest.raises(
        RuntimeError,
        match="^PEXELS_NO_DOWNLOAD_VIABLE_CANDIDATES$",
    ) as captured:
        GuardedProviderOperation(ledger).run(
            "pexels",
            gates=_passing_gates(),
            operation=lambda: client.search_select_once(
                api_key=api_key,
                request=_request(),
                workspace_directory=tmp_path / "workspace",
                semantic_fit_threshold=0.78,
            ),
        )

    safe_evidence = captured.value.safe_evidence
    assert (
        captured.value.safe_evidence_kind
        == "PEXELS_DOWNLOAD_VIABILITY_FAILURE"
    )
    persisted = json.loads(
        Path(safe_evidence["evidence_path"]).read_text(encoding="utf-8")
    )
    assert (
        persisted["schema_version"]
        == "vcos.pexels-download-viability-failure.v1"
    )
    assert persisted["reason_code"] == "PEXELS_NO_DOWNLOAD_VIABLE_CANDIDATES"
    assert persisted["query_plan_hash"]
    assert "query_plan" not in persisted
    assert persisted["technical_viability_filter"] == {
        "minimum_duration_seconds": 6.0,
        "input_candidate_count": 2,
        "viable_candidate_count": 0,
        "viable_provider_asset_ids": [],
        "rejected_candidates": [
            {
                "candidate_id": "pexels-9001",
                "provider_asset_id": "9001",
                "duration_seconds": 5.0,
                "minimum_duration_met": False,
                "compatible_mp4_rendition_found": True,
                "reason_codes": ["DURATION_BELOW_REQUEST_MINIMUM"],
            },
            {
                "candidate_id": "pexels-9002",
                "provider_asset_id": "9002",
                "duration_seconds": 9.0,
                "minimum_duration_met": True,
                "compatible_mp4_rendition_found": False,
                "reason_codes": ["PEXELS_COMPATIBLE_MP4_NOT_FOUND"],
            },
        ],
        "minimum_duration_filter_applied_before_ranking": True,
        "rendition_compatibility_filter_applied_before_ranking": True,
        "raw_media_urls_persisted": False,
    }
    durable_ledger = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert (
        durable_ledger["entries"]["pexels"]["evidence"][
            "pexels_download_viability_failure"
        ]["content_hash"]
        == persisted["content_hash"]
    )
    serialized = json.dumps(
        {"receipt": persisted, "ledger": durable_ledger},
        sort_keys=True,
    )
    assert api_key not in serialized
    assert media_token not in serialized
    assert "videos.pexels.test" not in serialized
    assert len(transport.calls) == 1
    assert client.search_flow_count == 1
    assert client.selected_download_count == 0
    assert downloader.calls == 0


def test_semantic_failure_persists_sanitized_query_and_ranking_receipt(tmp_path):
    api_key = "api-key-must-not-persist"
    media_token = "signed-media-secret-must-not-persist"
    transport = _SearchTransport(
        _live_video(
            asset_id=9002,
            slug="office-coworkers-review-generic-meeting",
            media_token=media_token,
        )
    )
    downloader = _NoDownload()
    client = PexelsPA1RClient(transport, downloader)
    ledger = PA1RCallLedger(tmp_path / "ledger.json")
    ledger.plan(
        "pexels",
        provider="pexels_api",
        operation="search",
        paid=False,
        idempotency_key="one-search",
    )

    with pytest.raises(
        RuntimeError,
        match="^PEXELS_SEMANTIC_FIT_INADEQUATE$",
    ) as captured:
        GuardedProviderOperation(ledger).run(
            "pexels",
            gates=_passing_gates(),
            operation=lambda: client.search_select_once(
                api_key=api_key,
                request=_request(),
                workspace_directory=tmp_path / "workspace",
                semantic_fit_threshold=0.78,
            ),
        )

    assert type(captured.value) is RuntimeError
    safe_evidence = captured.value.safe_evidence
    evidence_path = Path(safe_evidence["evidence_path"])
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    persisted_without_hash = dict(persisted)
    persisted_hash = persisted_without_hash.pop("content_hash")
    assert stable_hash(persisted_without_hash) == persisted_hash
    assert persisted["reason_code"] == "PEXELS_SEMANTIC_FIT_INADEQUATE"
    assert persisted["semantic_fit_gate"] == {
        "threshold": 0.78,
        "selected_candidate_id": None,
        "selected_semantic_relevance": None,
        "highest_ranked_candidate_id": "pexels-9002",
        "highest_ranked_semantic_relevance": 0.5,
        "result": "FAIL",
    }
    assert persisted["retrieval_evidence"]["query_used"]
    assert persisted["ranking"]["candidate_scores"]
    assert persisted["semantic_scoring_evidence"][0][
        "semantic_relevance"
    ] == 0.5
    assert persisted["sanitization"]["raw_media_urls_persisted"] is False
    assert safe_evidence["evidence_persisted"] is True

    durable_ledger = json.loads(ledger.path.read_text(encoding="utf-8"))
    failure = durable_ledger["entries"]["pexels"]["evidence"]
    assert failure["error_type"] == "RuntimeError"
    assert (
        failure["pexels_search_ranking_failure"]["content_hash"]
        == persisted["content_hash"]
    )
    serialized = json.dumps(
        {"receipt": persisted, "ledger": durable_ledger},
        sort_keys=True,
    )
    assert api_key not in serialized
    assert media_token not in serialized
    assert "videos.pexels.test" not in serialized
    assert client.search_flow_count == 1
    assert client.selected_download_count == 0
    assert downloader.calls == 0
    assert not list(evidence_path.parent.glob("*.part"))

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.contracts import (
    DestinationBinding,
    MarketGateKey,
    MarketGateResult,
    MarketPackageApproval,
    MarketReasonCode,
    MarketVerdict,
    MarketBoundPublishPackage,
    MinimalMarketChannelInit,
    PublishRiskMarketAlignment,
    TargetMarketDraftApproval,
)
from app.core.errors import ValidationFailureError
from app.services import (
    CompanyService,
    MarketAlignmentDossierBuilder,
    MarketChannelGovernanceService,
    MarketPackageFreezeService,
    TargetMarketDigestCompiler,
)
from app.services.geo_market import target_market_digest_ref, target_market_profile_ref


def _approved_scope(db_session):
    company = CompanyService(db_session).create_company(
        name="GEO2 package", slug="geo2-package"
    )
    service = MarketChannelGovernanceService(db_session)
    channel = service.create_minimal_channel(
        MinimalMarketChannelInit(
            company_id=company.id,
            channel_name="Small Team AI",
            channel_key="small-team-ai-geo2-package",
            channel_purpose="US market-native AI operations",
            primary_market="US",
            primary_language="en",
            primary_locale="en-US",
            target_audience_summary="US small teams",
            channel_market_type="MARKET_NATIVE",
            account_country="VN",
        )
    )
    draft = service.run_market_research_draft(channel.id)
    profile = service.approve_market_draft(
        channel.id,
        TargetMarketDraftApproval(
            expected_draft_id=draft.draft_id,
            expected_draft_version=draft.draft_version,
            expected_draft_hash=str(draft.content_hash),
            reviewer="operator",
            approval_ref="operator-approval://geo2/package/profile",
        ),
    )
    assert profile is not None
    digest = TargetMarketDigestCompiler().compile(profile)
    return channel, service, profile, digest


def _destination(channel, profile, *, status="VERIFIED") -> DestinationBinding:
    verified = status == "VERIFIED"
    return DestinationBinding(
        binding_version=1,
        channel_id=channel.id,
        channel_key=channel.key,
        platform="YOUTUBE",
        platform_account_ref="youtube-account://fixture" if verified else None,
        platform_channel_id="UC_FIXTURE_SMALL_TEAM_AI" if verified else None,
        channel_handle="@SmallTeamAI",
        account_country="VN",
        target_market_profile_ref=target_market_profile_ref(profile),
        target_market_profile_hash=str(profile.content_hash),
        target_market="US",
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status=status,
        credential_ref="credential://youtube/fixture" if verified else None,
        verification_state="VERIFIED" if verified else "PENDING",
        verification_timestamp=datetime.now(UTC) if verified else None,
        approval_ref="operator-approval://geo2/destination/fixture",
    )


def _dossier(profile, digest):
    results = []
    for index, gate_key in enumerate(
        [
            MarketGateKey.TOPIC_MARKET_ALIGNMENT_GATE,
            MarketGateKey.RESEARCH_JURISDICTION_GATE,
            MarketGateKey.SCRIPT_MARKET_ALIGNMENT_GATE,
            MarketGateKey.VOICE_LOCALE_ALIGNMENT_GATE,
            MarketGateKey.VISUAL_MARKET_ALIGNMENT_GATE,
            MarketGateKey.THUMBNAIL_MARKET_ALIGNMENT_GATE,
            MarketGateKey.METADATA_MARKET_ALIGNMENT_GATE,
        ],
        start=1,
    ):
        results.append(
            MarketGateResult(
                gate_key=gate_key,
                target_market_profile_ref=target_market_profile_ref(profile),
                target_market_profile_hash=str(profile.content_hash),
                target_market_digest_ref=target_market_digest_ref(profile),
                target_market_digest_hash=str(digest.content_hash),
                subject_ref=f"artifact://{index}",
                subject_hash=f"{index:x}" * 64,
                verdict=MarketVerdict.PASS,
            )
        )
    return MarketAlignmentDossierBuilder().build(
        profile=profile,
        digest=digest,
        channel_profile_version_ref="profile://v3",
        compiled_policy_snapshot_ref="snapshot://v3",
        compiled_policy_snapshot_hash="a" * 64,
        video_project_ref="project://1",
        video_project_hash="b" * 64,
        niche_alignment_dossier_ref="niche://1",
        niche_alignment_dossier_hash="c" * 64,
        component_results=results,
    )


def _risk(profile, destination):
    return PublishRiskMarketAlignment(
        target_market_profile_ref=target_market_profile_ref(profile),
        target_market_profile_hash=str(profile.content_hash),
        primary_market="US",
        destination_binding_ref=f"destination://{destination.content_hash}",
        destination_binding_hash=str(destination.content_hash),
        content_language_match=True,
        narration_locale_match=True,
        title_locale_match=True,
        thumbnail_locale_match=True,
        caption_language_match=True,
        currency_units_match=True,
        cultural_context_match=True,
        source_jurisdiction_match=True,
        topic_market_demand_match=True,
        publish_window_status="PASS",
        overall_decision=MarketVerdict.PASS,
    )


def _package(profile, destination, dossier, risk, *, file_ref="drive://video.mp4"):
    return MarketBoundPublishPackage(
        package_id="market-package-1",
        package_version=1,
        video_project_ref="project://1",
        media_file_ref=file_ref,
        media_file_hash="d" * 64 if file_ref else None,
        destination_binding_ref=f"destination://{destination.content_hash}",
        destination_binding_hash=str(destination.content_hash),
        target_market_profile_ref=target_market_profile_ref(profile),
        target_market_profile_hash=str(profile.content_hash),
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        caption_refs=[{"locale": "en-US", "ref": "caption://1"}],
        localized_metadata_refs=[{"locale": "en-US", "ref": "metadata://1"}],
        thumbnail_refs=[{"ref": "thumbnail://1", "hash": "e" * 64}],
        title="AI workflow for a small US team",
        description="A practical US small-business workflow.",
        disclosures=["Synthetic voice used"],
        approved_publish_timezone="America/New_York",
        approved_publish_window={"local": "2026-07-21T10:00:00-04:00", "utc": "2026-07-21T14:00:00Z"},
        market_alignment_dossier_ref="market-dossier://1",
        market_alignment_dossier_hash=str(dossier.content_hash),
        publish_risk_dossier_ref="publish-risk://1",
        publish_risk_dossier_hash=str(risk.content_hash),
        technical_media_qc="PASS",
        creative_human_review="PASS",
        market_alignment_verdict=MarketVerdict.PASS,
        publish_risk_verdict=MarketVerdict.PASS,
        destination_status=destination.destination_status,
        package_state="READY_FOR_APPROVAL",
    )


def test_destination_binding_is_typed_scoped_and_account_country_is_distinct(db_session) -> None:
    channel, service, profile, _digest = _approved_scope(db_session)
    destination = _destination(channel, profile)
    saved = service.save_destination_binding(channel.id, destination)
    assert saved.account_country == "VN"
    assert saved.target_market == "US"
    assert saved.manual_publish_required is True
    assert service.latest_destination_binding(channel.id) == saved

    wrong = destination.model_copy(
        update={"channel_id": channel.id, "channel_key": "wrong-channel", "binding_version": 2, "content_hash": None}
    )
    with pytest.raises(ValidationFailureError, match="CHANNEL_MISMATCH"):
        service.save_destination_binding(channel.id, wrong)


def test_exact_market_package_freeze_and_post_approval_integrity(db_session) -> None:
    channel, service, profile, digest = _approved_scope(db_session)
    destination = service.save_destination_binding(channel.id, _destination(channel, profile))
    dossier = _dossier(profile, digest)
    risk = _risk(profile, destination)
    package = _package(profile, destination, dossier, risk)
    freeze = MarketPackageFreezeService()
    target_hash = freeze.package_hash(package)
    frozen = freeze.freeze(
        package=package,
        approval=MarketPackageApproval(
            expected_package_id=package.package_id,
            expected_package_version=package.package_version,
            expected_package_hash=target_hash,
            expected_destination_binding_hash=str(destination.content_hash),
            expected_market_profile_hash=str(profile.content_hash),
            reviewer="operator",
            approval_ref="operator-approval://geo2/package/exact-v1",
        ),
        destination=destination,
        dossier=dossier,
        publish_risk=risk,
    )
    assert frozen.package_state == "MARKET_PACKAGE_FROZEN"
    assert frozen.approved_package_hash == target_hash
    assert freeze.verify_integrity(
        package=frozen,
        destination=destination,
        current_market_profile_hash=str(profile.content_hash),
    ).verdict == MarketVerdict.PASS

    changed = frozen.model_dump(mode="json", exclude={"content_hash"})
    changed["title"] = "Changed after approval"
    tampered = MarketBoundPublishPackage.model_validate(changed)
    integrity = freeze.verify_integrity(
        package=tampered,
        destination=destination,
        current_market_profile_hash=str(profile.content_hash),
    )
    assert integrity.verdict == MarketVerdict.BLOCK
    assert MarketReasonCode.MARKET_PACKAGE_INTEGRITY_MISMATCH in integrity.reason_codes


def test_unverified_wrong_or_missing_upload_input_blocks_freeze(db_session) -> None:
    channel, _service, profile, digest = _approved_scope(db_session)
    destination = _destination(channel, profile, status="PENDING_PLATFORM_ID")
    dossier = _dossier(profile, digest)
    risk = _risk(profile, destination)
    freeze = MarketPackageFreezeService()
    for package in (
        _package(profile, destination, dossier, risk),
        _package(profile, destination, dossier, risk, file_ref=None),
    ):
        with pytest.raises(ValidationFailureError, match="MARKET_PACKAGE_FREEZE_BLOCKED"):
            freeze.freeze(
                package=package,
                approval=MarketPackageApproval(
                    expected_package_id=package.package_id,
                    expected_package_version=package.package_version,
                    expected_package_hash=freeze.package_hash(package),
                    expected_destination_binding_hash=str(destination.content_hash),
                    expected_market_profile_hash=str(profile.content_hash),
                    reviewer="operator",
                    approval_ref="operator-approval://geo2/package/blocked",
                ),
                destination=destination,
                dossier=dossier,
                publish_risk=risk,
            )

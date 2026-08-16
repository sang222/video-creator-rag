from __future__ import annotations

import runpy
from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_generic_target_market_authority() -> None:
    "Add current typed market authority to the generic qualification fixture only."

    path = Path("tests/qualification/conftest.py")

    replace_once(
        path,
        "from app.contracts.channel_policy import ChannelScopedPolicy\n",
        '''from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.geo_market import (
    MARKET_GATE_STRICT_ORDER,
    DestinationBinding,
    TargetMarketDigest,
    TargetMarketProfile,
)
''',
        label="G1 geo-market contract imports",
    )

    replace_once(
        path,
        "from app.services.creative_quality_policy import CreativeQualityPolicyCatalog\n",
        '''from app.services.creative_quality_policy import CreativeQualityPolicyCatalog
from app.services.geo_market import (
    TargetMarketDigestCompiler,
    target_market_digest_ref_from_digest,
    target_market_profile_ref,
)
''',
        label="G1 geo-market service imports",
    )

    helper_anchor = "\n\ndef _generic_channel_policy(\n"
    helper = r'''


def _generic_market_policy_bundle(
    *,
    channel_id: uuid.UUID,
    channel_key: str,
    profile_input: ChannelProfileInput,
) -> dict:
    "Compile deterministic test market authority from the generic profile input."

    market = _market_locale(profile_input)
    primary_market = str(market.get("primary_market") or "").upper()
    primary_locale = str(
        market.get("audience_locale") or market.get("primary_locale") or ""
    )
    content_language = str(market.get("content_language") or "")
    primary_timezone = str(market.get("timezone") or "")
    currency = str(market.get("currency") or "")
    units_policy = str(market.get("measurement_units") or "")
    date_format = str(market.get("date_format") or "")
    if not all(
        (
            primary_market,
            primary_locale,
            content_language,
            primary_timezone,
            currency,
            units_policy,
            date_format,
        )
    ):
        raise RuntimeError("generic qualification market locale is incomplete")
    if (
        primary_locale != "en-US"
        or content_language != "en"
        or primary_timezone != "America/New_York"
    ):
        raise RuntimeError(
            "current strict publish-localization authority requires the "
            "configured en-US qualification market"
        )

    profile = TargetMarketProfile(
        profile_version=1,
        channel_id=channel_id,
        channel_key=channel_key,
        approval_ref=(
            f"operator-approval://qualification/{channel_key}/"
            "target-market-profile-v1"
        ),
        primary_market=primary_market,
        primary_geo_cluster=[primary_market],
        acceptable_secondary_geos=[],
        primary_locale=primary_locale,
        content_language=content_language,
        narration_locale=primary_locale,
        primary_timezone=primary_timezone,
        spelling_system=primary_locale.rsplit("-", 1)[-1],
        currency=currency,
        units_policy=units_policy,
        date_format=date_format,
        title_locale=primary_locale,
        thumbnail_text_locale=primary_locale,
        caption_locales=[primary_locale],
        audience_market_context=f"{primary_market}_PROFESSIONAL_TEAMS",
        workplace_context=f"{primary_market}_PROFESSIONAL_TEAMS",
        source_jurisdiction_policy=(
            "TARGET_MARKET_FIRST_CONTEXTUAL_FOREIGN_ALLOWED"
        ),
        preferred_source_jurisdictions=[primary_market],
        foreign_source_context_required=True,
        allowed_market_contexts=[primary_market],
        prohibited_market_mismatches=[
            "TRANSLATED_SOUNDING_ENGLISH",
            "NON_US_CURRENCY_WITHOUT_USD_EQUIVALENT",
            "FOREIGN_LEGAL_ASSUMPTION_WITHOUT_CONTEXT",
            "WRONG_VOICE_LOCALE",
            "WRONG_METADATA_LOCALE",
            "WRONG_THUMBNAIL_LOCALE",
        ],
        initial_publish_window_hypotheses=[],
        minimum_comparable_videos=3,
        video_geo_evaluation_window_days=7,
        channel_geo_review_window_days=30,
        account_country=None,
        target_market=primary_market,
        actual_viewer_geography_state="UNMEASURED",
    )
    digest = TargetMarketDigestCompiler().compile(profile)
    destination = DestinationBinding(
        binding_version=1,
        channel_id=channel_id,
        channel_key=channel_key,
        platform="YOUTUBE",
        platform_account_ref=None,
        platform_channel_id=None,
        channel_handle=None,
        account_country=None,
        target_market_profile_ref=target_market_profile_ref(profile),
        target_market_profile_hash=str(profile.content_hash),
        target_market=profile.primary_market,
        primary_market=profile.primary_market,
        primary_locale=profile.primary_locale,
        original_language=profile.content_language,
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status="PENDING_PLATFORM_ID",
        credential_ref=None,
        verification_state="NOT_VERIFIED",
        verification_timestamp=None,
        approval_ref=(
            f"operator-approval://qualification/{channel_key}/destination-v1"
        ),
    )
    return {
        "target_market_profile": profile.model_dump(mode="json"),
        "target_market_digest": digest.model_dump(mode="json"),
        "market_alignment_policy": {
            "required_gate_order": [
                gate.value for gate in MARKET_GATE_STRICT_ORDER
            ],
        },
        "destination_binding_policy": {
            "destination": destination.model_dump(mode="json"),
        },
        "market_package_freeze_policy": {
            "required_preconditions": [
                "TechnicalMediaQC.PASS",
                "CreativeHumanReview.PASS",
                "MarketAlignmentDossier.PASS",
                "DestinationBinding.VERIFIED",
                "PublishRiskDossier.PASS",
                "ExactPackageHumanApproval.PASS",
                "PostApprovalIntegrity.PASS",
            ],
            "frozen_fields": [
                "media_file_and_hash",
                "thumbnail_and_hash",
                "title",
                "description",
                "captions",
                "disclosures",
                "destination_binding",
                "target_market_profile",
                "publish_window",
                "package_hash",
            ],
        },
        "publish_timing_localization_policy": {
            "primary_timezone": profile.primary_timezone,
            "primary_locale": profile.primary_locale,
            "narration_locale": profile.narration_locale,
            "original_language": profile.content_language,
            "title_locale": profile.title_locale,
            "thumbnail_text_locale": profile.thumbnail_text_locale,
            "caption_locales": list(profile.caption_locales),
        },
        "geo_evaluation_policy": {
            "minimum_comparable_videos": profile.minimum_comparable_videos,
            "video_level_evaluation_window_days": (
                profile.video_geo_evaluation_window_days
            ),
            "channel_review_window_days": (
                profile.channel_geo_review_window_days
            ),
        },
    }


def _generic_channel_policy(
'''
    replace_once(
        path,
        helper_anchor,
        helper,
        label="G1 market authority helper",
    )

    replace_once(
        path,
        '''def _generic_channel_policy(
    *,
    channel_key: str,
''',
        '''def _generic_channel_policy(
    *,
    channel_id: uuid.UUID,
    channel_key: str,
''',
        label="G1 generic policy channel id",
    )

    replace_once(
        path,
        '''    return ChannelScopedPolicy.model_validate(policy)


class QualificationFactory:
''',
        '''    policy.update(
        _generic_market_policy_bundle(
            channel_id=channel_id,
            channel_key=channel_key,
            profile_input=profile_input,
        )
    )
    return ChannelScopedPolicy.model_validate(policy)


class QualificationFactory:
''',
        label="G1 attach market bundle",
    )

    replace_once(
        path,
        '''            policy = _generic_channel_policy(
                channel_key=channel.key,
''',
        '''            policy = _generic_channel_policy(
                channel_id=channel.id,
                channel_key=channel.key,
''',
        label="G1 pass channel id",
    )

    replace_once(
        path,
        '''        scope = self.channel_scope(name="M5", strict_long_form=True)
        profile_input = ChannelProfileInput.model_validate(scope.profile.profile_input)
''',
        '''        scope = self.channel_scope(name="M5", strict_long_form=True)
        scoped_policy = scope.snapshot.compiled_payload["channel_scoped_policy"]
        target_market_digest = TargetMarketDigest.model_validate(
            scoped_policy["target_market_digest"]
        )
        profile_input = ChannelProfileInput.model_validate(scope.profile.profile_input)
''',
        label="G1 resolve compiled target-market digest",
    )

    replace_once(
        path,
        '''                target_market_digest_ref=f"target-market://{scope.channel.id}/{primary_market}",
                target_market_digest_hash="b" * 64,
''',
        '''                target_market_digest_ref=target_market_digest_ref_from_digest(
                    target_market_digest
                ),
                target_market_digest_hash=str(target_market_digest.content_hash),
''',
        label="G1 bind exact target-market digest",
    )


def main() -> None:
    # Preserve the exact audited Round-2 implementation in a cleanup-listed
    # temporary artifact, then add only the G1 test-factory authority repair.
    runpy.run_path(
        "tools/closeout-hardening.recovery.err",
        run_name="__main__",
    )
    patch_generic_target_market_authority()


if __name__ == "__main__":
    main()

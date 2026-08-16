from __future__ import annotations

import re
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path


_ORIGINAL_ROUND3_BLOB = "c518a1d683921fdee4750280ff855707b0196ff6"


def _blob_text(blob_sha: str) -> str:
    return subprocess.check_output(
        ["git", "cat-file", "blob", blob_sha],
        text=True,
    )


def _run_original_round3() -> None:
    source = _blob_text(_ORIGINAL_ROUND3_BLOB)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="vcos-round3-",
        delete=False,
        encoding="utf-8",
    )
    try:
        with handle:
            handle.write(source)
        runpy.run_path(handle.name, run_name="__main__")
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _repair_round2_niche_digest_helper_boundary() -> None:
    """Undo only the next-function character consumed by the audited Round-2 regex."""

    path = Path("app/services/m5.py")
    text = path.read_text(encoding="utf-8")
    broken = "def _iche_digest_from_context(\n"
    current = "def _niche_digest_from_context(\n"
    broken_count = text.count(broken)
    current_count = text.count(current)
    if broken_count == 1 and current_count == 0:
        path.write_text(text.replace(broken, current, 1), encoding="utf-8")
        return
    if broken_count == 0 and current_count == 1:
        return
    raise SystemExit(
        "Round-2 niche digest helper boundary is ambiguous: "
        f"broken={broken_count}, current={current_count}"
    )


def _patch_current_evidence_authority_split() -> None:
    """Keep claim provenance and quantitative demand as separate authorities."""

    path = Path("tests/qualification/conftest.py")
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'''(?P<demand>        evidence = None\n'''
        r'''        if evidence_volume is not None:\n'''
        r'''            evidence = SearchDemandEvidenceService\(self\.session\)\.create_evidence\(\n'''
        r'''.*?'''
        r'''            \)\n)'''
        r'''(?=        research = EditorialResearchService\(self\.session\))''',
        re.S,
    )
    claim_block = r'''\g<demand>        claim_evidence = None
        if evidence_volume is not None:
            claim_evidence = SearchDemandEvidenceService(self.session).create_evidence(
                data=SearchDemandEvidenceCreate(
                    company_id=scope.company.id,
                    channel_workspace_id=scope.channel.id,
                    evidence_source_type="OFFICIAL_DOCUMENT",
                    authority_purpose="CLAIM_SOURCE",
                    source_ref=(
                        "https://docs.example.test/qualification/"
                        "approval-checkpoint"
                    ),
                    query="documented automation approval checkpoint",
                    platform="GENERIC",
                    geo=primary_market,
                    language=locale,
                    evidence_confidence="MEDIUM",
                )
            )
'''
    text, count = pattern.subn(claim_block, text, count=1)
    if count != 1:
        raise SystemExit(
            f"NV06 split claim evidence insertion expected 1 match, found {count}"
        )
    path.write_text(text, encoding="utf-8")

    _replace_once(
        path,
        '''                evidence_refs=[{"type": "search_demand_evidence", "id": str(evidence.id) if evidence else "missing"}],\n''',
        '''                evidence_refs=[{"type": "search_demand_evidence", "id": str(claim_evidence.id) if claim_evidence else "missing"}],\n''',
        label="NV06 candidate claim authority",
    )

    _replace_once(
        path,
        '''                evidence_blob={"search_demand_evidence_ids": [str(evidence.id)] if evidence is not None else []},\n''',
        '''                claim_evidence_refs=(\n                    [{"id": str(claim_evidence.id)}]\n                    if claim_evidence is not None\n                    else []\n                ),\n                market_demand_evidence_refs=(\n                    [{"id": str(evidence.id)}] if evidence is not None else []\n                ),\n                evidence_blob={},\n''',
        label="NV06 explicit split preflight authority",
    )


def _patch_current_voice_authority_fixture() -> None:
    """Seed current typed voice authority for strict generic qualification only."""

    path = Path("tests/qualification/conftest.py")
    _replace_once(
        path,
        "from app.contracts.m5 import (\n",
        '''from app.contracts.voice_authority import (
    ApprovedVoicePoolCreate,
    NarrationMarketRequirements,
    ProviderVoiceCandidate,
    VoiceMarketIdentity,
    VoiceMarketResearchCreate,
    VoiceProviderCatalogCreate,
    VoiceResearchEvidence,
)
from app.contracts.m5 import (
''',
        label="LF voice authority contract imports",
    )
    _replace_once(
        path,
        "from app.services.creative_quality_policy import CreativeQualityPolicyCatalog\n",
        '''from app.services.creative_quality_policy import CreativeQualityPolicyCatalog
from app.services.voice_authority import VoiceAuthorityService
''',
        label="LF voice authority service import",
    )

    helper = '''

def _seed_generic_voice_authority(
    session,
    *,
    company,
    channel,
    profile,
    snapshot,
    admin,
) -> None:
    """Create test-only typed voice authority without provider execution."""

    scoped = snapshot.compiled_payload.get("channel_scoped_policy")
    if not isinstance(scoped, dict):
        raise RuntimeError("generic qualification channel policy is missing")
    market = scoped.get("target_market_profile")
    voice_policy = scoped.get("voice_policy")
    if not isinstance(market, dict) or not isinstance(voice_policy, dict):
        raise RuntimeError("generic qualification voice/market authority is missing")

    primary_market = str(market.get("primary_market") or "")
    locale = str(market.get("narration_locale") or market.get("primary_locale") or "")
    language = str(market.get("content_language") or "")
    voice_id = str(voice_policy.get("voice_id") or "")
    voice_name = str(voice_policy.get("voice_name") or "Qualification Narrator")
    model_id = str(voice_policy.get("model_id") or "")
    settings = voice_policy.get("settings")
    if not all((primary_market, locale, language, voice_id, model_id)) or not isinstance(
        settings, dict
    ):
        raise RuntimeError("generic qualification voice authority is incomplete")

    narration_modes = [
        "TECHNICAL_EXPLAINER",
        "ANALYTICAL",
        "TACTICAL",
        "STORY_CASE_STUDY",
        "DOCUMENTARY",
        "CAUTIONARY",
    ]
    voice = ProviderVoiceCandidate(
        voice_id=voice_id,
        display_name=voice_name,
        language_tags=[language],
        locale_tags=[locale],
        accent_tags=[f"{primary_market}-neutral"],
        narration_mode_fit=narration_modes,
        market_fit_tags=[primary_market],
        clarity_score=95,
        energy_score=75,
        warmth_score=70,
        authority_score=90,
        conversationality_score=80,
        approved_model_ids=[model_id],
        default_model_id=model_id,
        default_settings=dict(settings),
        safe_setting_bounds={
            "speed": {"min": 0.85, "max": 1.10},
            "stability": {"min": 0.35, "max": 0.75},
            "similarity_boost": {"min": 0.65, "max": 0.90},
            "style": {"min": 0.0, "max": 0.20},
        },
        commercial_use_state="REQUIRES_APPROVED_PLAN",
        availability_state="AVAILABLE",
        priority=10,
        evidence_refs=[f"policy-snapshot://{snapshot.id}"],
    )

    authority = VoiceAuthorityService(session)
    research = authority.create_market_research(
        VoiceMarketResearchCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=snapshot.id,
            market_identity=VoiceMarketIdentity(
                primary_market=primary_market,
                target_countries=list(
                    market.get("primary_geo_cluster") or [primary_market]
                ),
                content_language=language,
                locale=locale,
                audience_profile={"fixture": "generic-long-form-qualification"},
                channel_positioning="Generic evidence-aware long-form qualification",
            ),
            requirements=NarrationMarketRequirements(
                accent_families=[f"{primary_market}-neutral"],
                pronunciation_locale=locale,
                clarity_profile="HIGH",
                pacing_profile="MEDIUM",
                energy_profile="MEDIUM",
                authority_profile="HIGH",
                warmth_profile="MEDIUM",
                conversationality_profile="HIGH",
                required_narration_modes=narration_modes,
            ),
            evidence=[
                VoiceResearchEvidence(
                    evidence_id=f"policy-snapshot:{snapshot.id}",
                    source_url=None,
                    source_title="Compiled generic qualification channel policy",
                    source_class="CHANNEL_POLICY",
                    excerpt=(
                        "The active test policy freezes market, locale, voice provider, "
                        "model and narration settings for strict long-form qualification."
                    ),
                    source_hash=str(snapshot.content_hash),
                )
            ],
            confidence_label="HIGH",
            limitations=[
                "Ephemeral test authority only; no provider or public platform call."
            ],
            created_by_user_id=admin.id,
        )
    )
    catalog = authority.create_provider_catalog(
        VoiceProviderCatalogCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            provider="elevenlabs",
            catalog_version=f"qualification-{channel.key}-v1",
            voices=[voice],
            source_refs=[f"policy-snapshot://{snapshot.id}"],
            created_by_user_id=admin.id,
        )
    )
    authority.create_approved_pool(
        ApprovedVoicePoolCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            channel_profile_version_id=profile.id,
            policy_snapshot_id=snapshot.id,
            voice_market_research_id=research.id,
            provider_catalog_snapshot_id=catalog.id,
            version=1,
            voices=[voice],
            approved_by_user_id=admin.id,
        )
    )


class QualificationFactory:
'''
    _replace_once(
        path,
        "\n\nclass QualificationFactory:\n",
        helper,
        label="LF generic voice authority helper",
    )

    _replace_once(
        path,
        '''        snapshot = profiles.activate_snapshot(snapshot_id=compiled.snapshot_id)
        profile = profiles.get_profile_version(profile.id)
        return SimpleNamespace(company=company, channel=channel, profile=profile, snapshot=snapshot, operator=operator, admin=admin, compiled=compiled)
''',
        '''        snapshot = profiles.activate_snapshot(snapshot_id=compiled.snapshot_id)
        profile = profiles.get_profile_version(profile.id)
        if strict_long_form:
            _seed_generic_voice_authority(
                self.session,
                company=company,
                channel=channel,
                profile=profile,
                snapshot=snapshot,
                admin=admin,
            )
        return SimpleNamespace(company=company, channel=channel, profile=profile, snapshot=snapshot, operator=operator, admin=admin, compiled=compiled)
''',
        label="LF seed typed voice authority",
    )


def _patch_current_long_form_cost_authority_fixture() -> None:
    """Scope synthetic reviewed provider costs to LF integration tests only."""

    path = Path("tests/test_long_form_launch_cadence.py")
    _replace_once(
        path,
        "from app.core.errors import ValidationFailureError\n",
        '''from app.core.config import get_settings
from app.core.errors import ValidationFailureError
''',
        label="LF reviewed cost authority settings import",
    )
    _replace_once(
        path,
        '''@pytest.fixture
def qualification_factory(db_session):
    return QualificationFactory(db_session)
''',
        '''@pytest.fixture
def qualification_factory(db_session, monkeypatch):
    """Provide reviewed synthetic TTS cost authority only for LF integration."""

    monkeypatch.setenv(
        "VCOS_ELEVENLABS_TTS_COST_PER_CHARACTER_USD",
        "0.010000",
    )
    monkeypatch.setenv(
        "VCOS_ELEVENLABS_FORCED_ALIGNMENT_COST_USD",
        "0.250000",
    )
    get_settings.cache_clear()
    try:
        yield QualificationFactory(db_session)
    finally:
        get_settings.cache_clear()
''',
        label="LF scoped reviewed TTS cost authority",
    )


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    _repair_round2_niche_digest_helper_boundary()
    _run_original_round3()
    if stage == "rc2":
        _patch_current_evidence_authority_split()
        _patch_current_voice_authority_fixture()
        _patch_current_long_form_cost_authority_fixture()


if __name__ == "__main__":
    main()
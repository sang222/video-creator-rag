from __future__ import annotations

import argparse
import re
from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_rc2_test_data_factory() -> None:
    """Use current quantitative demand authority in the generic test factory only."""

    path = Path("tests/qualification/conftest.py")
    replace_once(
        path,
        '''                    evidence_source_type="MANUAL_RESEARCH",
                    authority_purpose="MARKET_DEMAND",
''',
        '''                    evidence_source_type="GOOGLE_TRENDS_CSV",
                    authority_purpose="MARKET_DEMAND",
''',
        label="RC-2 quantitative market-demand fixture",
    )


def _insert_nich1_visual_resolver(text: str) -> str:
    text = text.replace(
        "from pydantic import BaseModel\n",
        "from pydantic import BaseModel, ValidationError\n",
        1,
    )
    import_anchor = "from app.contracts.nich1 import (\n"
    if "from app.contracts.channel_policy import ChannelVisualStrategyProfile\n" not in text:
        text = text.replace(
            import_anchor,
            "from app.contracts.channel_policy import ChannelVisualStrategyProfile\n"
            + import_anchor,
            1,
        )
    nich1_import_end = "    nich1_stable_hash,\n)\n"
    if "from app.contracts.visual_routing import NicheVisualSourceProfile\n" not in text:
        text = text.replace(
            nich1_import_end,
            nich1_import_end
            + "from app.contracts.visual_routing import NicheVisualSourceProfile\n",
            1,
        )

    helper_anchor = '''def _profile_input(profile_version: Any) -> dict[str, Any]:
    return _as_dict(_get(profile_version, "profile_input", {}))


'''
    helper = '''def _profile_input(profile_version: Any) -> dict[str, Any]:
    return _as_dict(_get(profile_version, "profile_input", {}))


def _resolve_niche_visual_authority(
    *,
    compiled_channel_policy: Mapping[str, Any],
    profile_input: Mapping[str, Any],
    category: Any,
    media: Mapping[str, Any],
) -> str:
    """Normalize current generic visual authority before historical fallback.

    Current profiles are authoritative through the typed
    ``ChannelVisualStrategyProfile`` compiled into the immutable channel-policy
    snapshot. Historical ``niche_visual_source_profile`` values remain readable
    only as a fallback for durable old records; they are never required for new
    generic profiles and never outrank a valid current strategy.
    """

    current_raw = _as_dict(compiled_channel_policy.get("channel_visual_strategy_profile"))
    if current_raw:
        try:
            current = ChannelVisualStrategyProfile.model_validate(current_raw)
        except ValidationError:
            current = None
        if current is not None:
            strategy_label = _clean(current.strategy_label)
            if strategy_label:
                return strategy_label

    legacy_binding = _as_dict(compiled_channel_policy.get("visual_source_policy_binding"))
    legacy_candidates = (
        legacy_binding.get("niche_visual_source_profile"),
        _as_dict(profile_input.get("media_style")).get("niche_visual_source_profile"),
        _as_dict(_get(category, "default_visual_style_json", {})).get(
            "niche_visual_source_profile"
        ),
        media.get("niche_visual_source_profile"),
    )
    for candidate in legacy_candidates:
        value = _clean(candidate)
        if not value:
            continue
        try:
            return NicheVisualSourceProfile(value).value
        except ValueError:
            continue

    raise NicheContractCompilationError(
        [NicheReasonCode.NICHE_CONTRACT_REQUIRED_FIELD_MISSING],
        "visual_source_profile",
    )


'''
    if "def _resolve_niche_visual_authority(" not in text:
        if helper_anchor not in text:
            raise SystemExit("RC-1 visual resolver insertion anchor not found")
        text = text.replace(helper_anchor, helper, 1)
    return text


def patch_rc1_nich1_visual_authority() -> None:
    """Make generic compiled visual strategy primary, with validated legacy fallback."""

    path = Path("app/services/nich1.py")
    text = _insert_nich1_visual_resolver(path.read_text(encoding="utf-8"))

    old_compiled = '''        compiled_visual = _as_dict(
            compiled_channel_policy.get("channel_visual_strategy_profile")
        )
        compiled_visual_binding = _as_dict(
            compiled_channel_policy.get("visual_source_policy_binding")
        )

'''
    if old_compiled not in text:
        raise SystemExit("RC-1 legacy compiled visual locals not found")
    text = text.replace(old_compiled, "", 1)

    pattern = re.compile(
        r'''        visual_source_profile = _clean\(\n'''
        r'''            compiled_visual_binding\.get\("niche_visual_source_profile"\)\n'''
        r'''            or compiled_visual\.get\("niche_visual_source_profile"\)\n'''
        r'''            or _as_dict\(profile_input\.get\("media_style"\)\)\.get\(\n'''
        r'''                "niche_visual_source_profile"\n'''
        r'''            \)\n'''
        r'''            or _as_dict\(_get\(category, "default_visual_style_json", \{\}\)\)\.get\(\n'''
        r'''                "niche_visual_source_profile"\n'''
        r'''            \)\n'''
        r'''            or media\.get\("niche_visual_source_profile"\)\n'''
        r'''        \)\n'''
    )
    replacement = '''        visual_source_profile = _resolve_niche_visual_authority(
            compiled_channel_policy=compiled_channel_policy,
            profile_input=profile_input,
            category=category,
            media=media,
        )
'''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"RC-1 visual authority call replacement expected 1, found {count}")
    path.write_text(text, encoding="utf-8")

    Path("tests/test_nich1_visual_authority.py").write_text(
        '''from __future__ import annotations

import pytest

from app.services.nich1 import (
    NicheContractCompilationError,
    _resolve_niche_visual_authority,
)


def _strategy(label: str) -> dict:
    return {
        "strategy_label": label,
        "native_explanatory_target_range": {"minimum": 0.35, "maximum": 0.70},
        "supporting_visual_target_range": {"minimum": 0.0, "maximum": 0.20},
        "ai_hero_target_range": {"minimum": 0.20, "maximum": 0.60},
        "ranges_are_planning_guidance_only": True,
        "minimum_pexels_quota": 0,
        "minimum_veo_quota": 0,
        "asset_selected_only_to_satisfy_ratio": False,
        "native_preferred_scene_kinds": ["mechanism", "data", "workflow"],
        "pexels_allowed_scene_kinds": ["supporting context only"],
        "veo_allowed_scene_kinds": ["hero", "metaphor"],
        "forced_provider_alternation": False,
    }


def _resolve(policy: dict, *, profile_input: dict | None = None, media: dict | None = None) -> str:
    return _resolve_niche_visual_authority(
        compiled_channel_policy=policy,
        profile_input=profile_input or {},
        category={},
        media=media or {},
    )


def test_current_generic_visual_strategy_compiles_without_legacy_profile() -> None:
    label = "ai-authored-native-composition"
    assert _resolve({"channel_visual_strategy_profile": _strategy(label)}) == label


def test_current_generic_visual_strategy_requires_no_ch1_binding() -> None:
    policy = {"channel_visual_strategy_profile": _strategy("native-evidence-first")}
    assert "visual_source_policy_binding" not in policy
    assert _resolve(policy) == "native-evidence-first"


def test_missing_current_and_historical_visual_authority_fails_closed() -> None:
    with pytest.raises(
        NicheContractCompilationError,
        match="NICHE_CONTRACT_REQUIRED_FIELD_MISSING:visual_source_profile",
    ):
        _resolve({})


def test_current_generic_visual_strategy_wins_over_legacy_fallback() -> None:
    policy = {
        "channel_visual_strategy_profile": _strategy("current-generic-ai-native"),
        "visual_source_policy_binding": {
            "niche_visual_source_profile": "STOCK_ASSISTED"
        },
    }
    assert _resolve(policy) == "current-generic-ai-native"


def test_two_generic_profiles_resolve_distinct_visual_strategies_same_path() -> None:
    first = _resolve(
        {"channel_visual_strategy_profile": _strategy("ai-native-explainer")}
    )
    second = _resolve(
        {"channel_visual_strategy_profile": _strategy("authority-asset-native-explainer")}
    )
    assert first == "ai-native-explainer"
    assert second == "authority-asset-native-explainer"
    assert first != second
''',
        encoding="utf-8",
    )


def harden_permanent_ci() -> None:
    """Make exact pushed SHA enforce the same closeout gates as the finalizer."""

    Path(".github/workflows/remaining-debt-closeout.yml").write_text(
        '''name: Remaining Debt Closeout

on:
  push:
    branches:
      - feat/vcos-remaining-debt-closeout
  pull_request:
    branches:
      - main

permissions:
  contents: read

jobs:
  series-learning-business-scale:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: vcos
          POSTGRES_PASSWORD: vcos
          POSTGRES_DB: vcos
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U vcos -d vcos"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      VCOS_DATABASE_URL: postgresql+psycopg://vcos:vcos@localhost:5432/vcos
      VCOS_ENVIRONMENT: test
      VCOS_DISABLE_MEDIA_PROVIDER_CALLS: "true"
      VCOS_DISABLE_UPLOAD_AND_PUBLISH: "true"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
      - name: Install runtime and native regression dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e ".[dev]" ruff==0.15.12
          sudo apt-get update
          sudo apt-get install --yes ffmpeg
      - name: Compile
        run: python -m compileall -q app tests
      - name: Migrate single head
        run: |
          alembic upgrade head
          test "$(alembic heads | wc -l | tr -d ' ')" = "1"
          test "$(alembic heads | awk '{print $1}')" = "0087_business_os"
      - name: Direct NICH1 visual authority regressions
        run: pytest -q tests/test_nich1_visual_authority.py
      - name: Targeted closeout and critical production regressions
        shell: bash
        run: |
          set -euo pipefail
          files=(tests/test_remaining_debt_closeout.py tests/test_one_engine_many_profiles.py)
          for candidate in \
            tests/test_long_form_launch_cadence.py \
            tests/test_production_publish.py \
            tests/test_youtube_private_delivery.py \
            tests/test_editorial_novelty.py \
            tests/test_cross_modal_lineage.py \
            tests/test_v2_renderer_reconciliation.py \
            tests/test_voice_authority.py \
            tests/test_voice_execution.py; do
            if [[ -f "$candidate" ]]; then files+=("$candidate"); fi
          done
          pytest -q "${files[@]}"
      - name: Full repository regression
        run: pytest -q
      - name: Clean PostgreSQL migration proof
        shell: bash
        run: |
          set -euo pipefail
          python - <<'PY'
          import psycopg
          with psycopg.connect(
              "postgresql://vcos:vcos@localhost:5432/postgres", autocommit=True
          ) as connection:
              connection.execute("DROP DATABASE IF EXISTS vcos_clean_verify")
              connection.execute("CREATE DATABASE vcos_clean_verify")
          PY
          VCOS_DATABASE_URL=postgresql+psycopg://vcos:vcos@localhost:5432/vcos_clean_verify alembic upgrade head
          test "$(alembic heads | wc -l | tr -d ' ')" = "1"
          test "$(alembic heads | awk '{print $1}')" = "0087_business_os"
      - name: Architecture audit
        run: |
          python - <<'PY'
          from pathlib import Path
          from app.services.remaining_debt_closeout import ArchitectureDebtAuditService
          result = ArchitectureDebtAuditService().audit(Path.cwd())
          assert result.one_engine_many_profiles, result
          print(result)
          PY
      - name: Ruff
        run: |
          ruff check --isolated \
            app/services/m5.py \
            app/services/nich1.py \
            app/services/profile_compiler.py \
            app/services/production_publish.py \
            app/services/remaining_debt_closeout.py \
            tests/conftest.py \
            tests/qualification/conftest.py \
            tests/test_nich1_visual_authority.py \
            tests/test_one_engine_many_profiles.py \
            tests/test_remaining_debt_closeout.py \
            tests/test_voice_authority.py
''',
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("rc2", "rc1", "ci"))
    args = parser.parse_args()
    if args.stage == "rc2":
        patch_rc2_test_data_factory()
    elif args.stage == "rc1":
        patch_rc1_nich1_visual_authority()
    else:
        harden_permanent_ci()


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.native_render_plan import stable_hash


STRATEGIES: dict[str, dict[str, Any]] = {
    "NR2_A_NATIVE_EXPLANATORY": {"roles": ["NATIVE"] * 6 + ["SUPPORTING"], "cost": "LOW"},
    "NR2_B_BALANCED": {"roles": ["NATIVE"] * 4 + ["SUPPORTING"] * 2 + ["HERO"], "cost": "MEDIUM"},
    "NR2_C_HERO_HEAVY_PLACEHOLDER": {"roles": ["NATIVE"] * 2 + ["SUPPORTING"] * 2 + ["HERO"] * 3, "cost": "HIGH"},
}
ROLE_SOURCE = {"NATIVE": "NATIVE", "SUPPORTING": "PEXELS", "HERO": "GOOGLE_VEO"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distribution(roles: list[str]) -> dict[str, float]:
    total = len(roles)
    return {key.lower(): round(roles.count(key) / total, 6) for key in ("NATIVE", "SUPPORTING", "HERO")}


def strategy_distribution_gate(strategy_key: str, roles: list[str]) -> str:
    d = distribution(roles)
    bounds = {
        # Seven equal narrative units make the closest deterministic A split 6/1/0.
        # The +1 percentage-point discretization tolerance is explicit and bounded.
        "NR2_A_NATIVE_EXPLANATORY": ((.70, .86), (0, .15), (0, .05)),
        "NR2_B_BALANCED": ((.45, .60), (.20, .30), (.10, .20)),
        "NR2_C_HERO_HEAVY_PLACEHOLDER": ((.25, .40), (.15, .30), (.30, .45)),
    }[strategy_key]
    values = (d["native"], d["supporting"], d["hero"])
    return "PASS" if all(low <= value <= high for value, (low, high) in zip(values, bounds)) else "BLOCK"


def placeholder_truthfulness(asset: dict[str, Any]) -> str:
    future = asset["planned_future_source"]
    actual = asset["actual_NR2_source"]
    if future in {"GOOGLE_VEO", "PEXELS"}:
        ok = actual == "LOCAL_PLACEHOLDER" and asset.get("provider_quality_not_evaluated") is True and asset.get("production_eligible") is False
        if future == "GOOGLE_VEO":
            ok = ok and asset.get("asset_status") == "LOCAL_HERO_PLACEHOLDER" and asset.get("not_provider_generated") is True
        return "PASS" if ok else "BLOCK"
    return "PASS" if future == "NATIVE" and actual == "LOCAL_SYNTHETIC" else "BLOCK"


def validate_same_content(plans: list[dict[str, Any]]) -> str:
    shared = ("script_ref", "script_hash", "audio_ref", "audio_hash", "srt_ref", "srt_hash", "timing_hash", "output_profile")
    fingerprints = {stable_hash({key: plan[key] for key in shared}) for plan in plans}
    return "PASS" if len(fingerprints) == 1 else "BLOCK"


def validate_strategy_risks(strategy_key: str, roles: list[str], *, transition_count: int) -> dict[str, str]:
    d = distribution(roles)
    explanation = "PASS" if roles.count("NATIVE") >= 2 else "BLOCK"
    stock = "BLOCK" if d["supporting"] > .30 else "PASS"
    hero = "REVIEW_REQUIRED" if strategy_key == "NR2_C_HERO_HEAVY_PLACEHOLDER" and d["hero"] >= .30 else "PASS"
    motion = "REVIEW_REQUIRED" if transition_count > len(roles) else "PASS"
    return {"ExplanationCoverageGate": explanation, "StockBackboneRiskGate": stock, "HeroOveruseRiskGate": hero, "MotionOverloadGate": motion}


def plan_diff_manifest(plans: list[dict[str, Any]]) -> dict[str, Any]:
    allowed = {"strategy_key", "visual_treatment", "asset_slot_mapping", "animation_preset", "transition_preset", "emphasis_targets", "projected_provider_intent", "plan_id", "plan_hash"}
    keys = set().union(*(plan.keys() for plan in plans))
    changed = sorted(key for key in keys if len({json.dumps(plan.get(key), sort_keys=True) for plan in plans}) > 1)
    unexpected = sorted(set(changed) - allowed)
    return {"changed_fields": changed, "allowed_changed_fields": sorted(allowed), "unexpected_differences": unexpected, "complete": not unexpected, "strategies": {p["strategy_key"]: {k: p.get(k) for k in changed} for p in plans}}


def assert_local_output(workspace: Path, output: Path) -> Path:
    root = workspace.resolve()
    candidate = output.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("OUTPUT_PATH_ESCAPE")
    return candidate

"""Canonical identities shared by first-launch authority consumers."""

from __future__ import annotations

from app.db.models.launch_cadence import FirstChannelLaunchPolicyVersion, LaunchRun
from app.services.config_registry import content_hash


def launch_run_authority_hash(
    *,
    launch_policy: FirstChannelLaunchPolicyVersion,
    launch_run: LaunchRun,
) -> str:
    """Return the canonical immutable identity for an active launch run.

    State and reason codes remain part of this existing authority identity.
    Strict preflight separately requires an ACTIVE run, so a terminal state
    can never be hidden by a historical matching hash.
    """

    return content_hash(
        {
            "launch_key": launch_run.launch_key,
            "launch_policy_hash": launch_policy.canonical_hash,
            "launch_policy_version_id": str(launch_policy.id),
            "launch_run_id": str(launch_run.id),
            "launch_started_at": (
                launch_run.launch_started_at.isoformat()
                if launch_run.launch_started_at is not None
                else None
            ),
            "preparation_started_on": launch_run.preparation_started_on.isoformat(),
            "reason_codes": list(launch_run.reason_codes or []),
            "state": launch_run.state,
        }
    )

"""Long-form editorial research and controlled-evidence candidate runway."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.m5 import (
    EditorialIdeaCandidateCreate,
    EditorialIdeaCandidateTransition,
    EditorialResearchRunCreate,
)
from app.contracts.vcos_v2 import (
    DecisionReversibility,
    StrategicIntent,
    StrategicLineageV2,
)
from app.core.actor import ActorContext, ActorType
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.m5 import (
    EditorialIdeaCandidate,
    EditorialResearchRun,
    IdeaMarketPreflight,
)
from app.db.models.m7 import UploadedVideo
from app.db.models.launch_cadence import FirstChannelLaunchPolicyVersion, LaunchRun
from app.services.company_access import require_company_permission
from app.services.config_registry import content_hash


_CANDIDATE_TRANSITIONS = {
    "RESEARCHED": {"PREFLIGHT_PASS", "PREFLIGHT_BLOCK", "REJECTED", "EXPIRED"},
    "PREFLIGHT_PASS": {"GREENLIT", "REJECTED", "EXPIRED"},
    "PREFLIGHT_BLOCK": {"REJECTED", "EXPIRED"},
    "GREENLIT": {"SELECTED_FOR_SLOT", "REJECTED", "EXPIRED"},
    "SELECTED_FOR_SLOT": {"IN_PRODUCTION", "GREENLIT", "REJECTED", "EXPIRED"},
    "IN_PRODUCTION": {"FINAL_REVIEW_READY", "REJECTED"},
    "FINAL_REVIEW_READY": {"PUBLISHED", "REJECTED"},
    "PUBLISHED": set(),
    "REJECTED": set(),
    "EXPIRED": set(),
}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class EditorialResearchService:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        *,
        data: EditorialResearchRunCreate,
        actor: ActorContext,
    ) -> EditorialResearchRun:
        self._authorize_company(company_id=data.company_id, actor=actor)
        workspace = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        profile = self.session.get(
            ChannelProfileVersion, data.channel_profile_version_id
        )
        policy = self.session.get(
            CompiledChannelPolicySnapshot, data.policy_snapshot_id
        )
        if (
            workspace is None
            or workspace.company_id != data.company_id
            or profile is None
            or profile.channel_workspace_id != workspace.id
            or policy is None
            or policy.channel_workspace_id != workspace.id
            or policy.channel_profile_version_id != profile.id
            or profile.status not in {"approved", "active"}
            or policy.status not in {"approved", "active"}
        ):
            raise ValidationFailureError("EDITORIAL_RESEARCH_AUTHORITY_MISMATCH")
        payload = data.model_dump()
        metadata = payload.pop("metadata")
        record = EditorialResearchRun(
            **payload,
            metadata_=metadata,
            candidate_count=0,
            # A system worker is a trusted execution identity, not a User row.
            # Keep the nullable user FK truthful while the run metadata/audit
            # records retain the automation provenance.
            created_by_user_id=(
                actor.actor_id
                if actor.actor_type == ActorType.HUMAN_USER
                else None
            ),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def start_run(
        self, *, run_id: uuid.UUID, actor: ActorContext
    ) -> EditorialResearchRun:
        run = self._locked_run(run_id)
        self._authorize(run, actor)
        if run.status == "RUNNING":
            return run
        if run.status != "PENDING":
            raise ConflictError(f"research run cannot start from {run.status}")
        run.status = "RUNNING"
        run.started_at = utc_now()
        run.reason_codes = ["EDITORIAL_RESEARCH_STARTED"]
        self.session.flush()
        return run

    def add_candidate(
        self,
        *,
        data: EditorialIdeaCandidateCreate,
        actor: ActorContext,
    ) -> EditorialIdeaCandidate:
        run = self._locked_run(data.editorial_research_run_id)
        self._authorize(run, actor)
        if run.status not in {"PENDING", "RUNNING"}:
            raise ConflictError(
                f"research run does not accept candidates: {run.status}"
            )
        if data.stage != "RESEARCHED":
            raise ValidationFailureError("EDITORIAL_CANDIDATE_MUST_START_RESEARCHED")
        launch_authority = self._active_launch_authority(run)
        launch_policy, launch_run = (
            launch_authority if launch_authority is not None else (None, None)
        )
        if data.suggested_series_plan_id is not None:
            from app.db.models.vcos_v2 import SeriesPlan

            plan = self.session.get(SeriesPlan, data.suggested_series_plan_id)
            if (
                plan is None
                or plan.company_id != run.company_id
                or plan.channel_workspace_id != run.channel_workspace_id
                or plan.policy_snapshot_id != run.policy_snapshot_id
                or plan.state != "APPROVED"
                or plan.allowed_production_lanes != ["LONG_FORM"]
            ):
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_SERIES_AUTHORITY_MISMATCH"
                )
            if launch_policy is not None and str(
                data.suggested_series_plan_id
            ) not in set(launch_policy.approved_initial_series_plan_ids or []):
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_SERIES_OUTSIDE_LAUNCH_POLICY"
                )
        published_count = int(
            self.session.scalar(
                select(func.count(UploadedVideo.id)).where(
                    UploadedVideo.channel_workspace_id == run.channel_workspace_id,
                    UploadedVideo.verification_status == "VERIFIED",
                )
            )
            or 0
        )
        experiment_video_count = (
            launch_policy.first_n_public_videos if launch_policy is not None else 10
        )
        audience_phase_end = max(
            1,
            (experiment_video_count * 3 + 9) // 10,
        )
        series_phase_end = max(
            audience_phase_end,
            (experiment_video_count * 7 + 9) // 10,
        )
        expected_phase = (
            "AUDIENCE_PROMISE"
            if published_count < audience_phase_end
            else "SERIES_PACKAGING"
            if published_count < series_phase_end
            else "ALLOCATION_PREPARATION"
            if published_count < experiment_video_count
            else "STEADY_STATE"
        )
        if (
            data.experiment_phase is not None
            and data.experiment_phase != expected_phase
        ):
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_EXPERIMENT_PHASE_MISMATCH"
            )
        if data.primary_variable_under_test and (
            not data.baseline_refs or not data.comparison_group
        ):
            raise ValidationFailureError(
                "EXPERIMENT_VARIABLE_REQUIRES_BASELINE_AND_COMPARISON"
            )
        server_lineage: StrategicLineageV2 | None = None
        if launch_policy is not None and launch_run is not None:
            server_lineage = self._resolve_server_lineage(
                data=data,
                run=run,
                launch_policy=launch_policy,
                launch_run=launch_run,
                expected_phase=expected_phase,
                published_count=published_count,
            )
        elif any(
            value is not None
            for value in data.model_dump(
                mode="python",
                include=set(StrategicLineageV2.model_fields),
            ).values()
        ):
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_STRATEGIC_LINEAGE_REQUIRES_ACTIVE_LAUNCH"
            )
        normalized_candidate = data.model_copy(
            update={
                "budget_readiness": "UNKNOWN",
                "rights_policy_state": "UNKNOWN",
                "quality_state": "UNKNOWN",
                **(
                    server_lineage.model_dump(mode="python")
                    if server_lineage is not None
                    else {}
                ),
            }
        )
        semantic = {
            **normalized_candidate.model_dump(mode="json"),
            "company_id": str(run.company_id),
            "channel_workspace_id": str(run.channel_workspace_id),
            "policy_snapshot_id": str(run.policy_snapshot_id),
        }
        digest = _canonical_hash(semantic)
        existing = self.session.scalar(
            select(EditorialIdeaCandidate).where(
                EditorialIdeaCandidate.canonical_hash == digest
            )
        )
        if existing is not None:
            return existing
        payload = normalized_candidate.model_dump(exclude={"experiment_phase"})
        record = EditorialIdeaCandidate(
            **payload,
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            policy_snapshot_id=run.policy_snapshot_id,
            experiment_phase=data.experiment_phase or expected_phase,
            canonical_hash=digest,
            created_by_user_id=actor.actor_id,
        )
        self.session.add(record)
        run.candidate_count += 1
        self.session.flush()
        return record

    def _active_launch_authority(
        self,
        run: EditorialResearchRun,
    ) -> tuple[FirstChannelLaunchPolicyVersion, LaunchRun] | None:
        """Lock the active launch authority when this is a launch-era candidate."""

        launch_policy = self.session.scalar(
            select(FirstChannelLaunchPolicyVersion)
            .where(
                FirstChannelLaunchPolicyVersion.company_id == run.company_id,
                FirstChannelLaunchPolicyVersion.channel_workspace_id
                == run.channel_workspace_id,
                FirstChannelLaunchPolicyVersion.channel_profile_version_id
                == run.channel_profile_version_id,
                FirstChannelLaunchPolicyVersion.policy_snapshot_id
                == run.policy_snapshot_id,
                FirstChannelLaunchPolicyVersion.state == "APPROVED",
            )
            .with_for_update()
        )
        if launch_policy is None:
            return None
        launch_run = self.session.scalar(
            select(LaunchRun)
            .where(
                LaunchRun.launch_policy_version_id == launch_policy.id,
                LaunchRun.company_id == run.company_id,
                LaunchRun.channel_workspace_id == run.channel_workspace_id,
                LaunchRun.state == "ACTIVE",
            )
            .with_for_update()
        )
        if launch_run is None:
            return None
        return launch_policy, launch_run

    @staticmethod
    def _launch_run_authority_hash(
        *,
        launch_policy: FirstChannelLaunchPolicyVersion,
        launch_run: LaunchRun,
    ) -> str:
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

    @staticmethod
    def _audience_authority(
        *,
        policy: CompiledChannelPolicySnapshot,
    ) -> dict[str, Any]:
        compiled = policy.compiled_payload or {}
        contract = compiled.get("channel_contract_json")
        if not isinstance(contract, dict):
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_AUDIENCE_CONTRACT_REQUIRED"
            )
        identity = contract.get("channel_identity")
        target = contract.get("target_audience")
        market = contract.get("market_locale")
        if not isinstance(identity, dict) or not isinstance(target, dict):
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_AUDIENCE_CONTRACT_REQUIRED"
            )
        audience_promise = identity.get("brand_promise")
        primary_persona = target.get("primary_persona")
        if not isinstance(audience_promise, str) or not audience_promise.strip():
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_AUDIENCE_PROMISE_REQUIRED"
            )
        if not isinstance(primary_persona, str) or not primary_persona.strip():
            raise ValidationFailureError("EDITORIAL_CANDIDATE_TARGET_AUDIENCE_REQUIRED")
        target_definition = {
            "audience_level": target.get("audience_level"),
            "audience_notes": target.get("audience_notes"),
            "desired_outcome": target.get("desired_outcome"),
            "market_locale": {
                "audience_locale": (
                    market.get("audience_locale") if isinstance(market, dict) else None
                ),
                "content_language": (
                    market.get("content_language") if isinstance(market, dict) else None
                ),
                "primary_market": (
                    market.get("primary_market") if isinstance(market, dict) else None
                ),
            },
            "pain_points": list(target.get("pain_points") or []),
            "primary_persona": primary_persona.strip(),
        }
        audience_promise_version = (
            f"channel-contract-snapshot-{policy.snapshot_version}"
        )
        audience_drift_guard_version = (
            f"channel-contract-drift-guard-{policy.snapshot_version}"
        )
        return {
            "audience_promise": audience_promise.strip(),
            "audience_promise_version": audience_promise_version,
            "audience_promise_hash": StrategicLineageV2.calculate_audience_promise_hash(
                audience_promise=audience_promise.strip(),
                audience_promise_version=audience_promise_version,
                target_audience_definition=target_definition,
                audience_drift_guard_version=audience_drift_guard_version,
            ),
            "target_audience_definition": target_definition,
            "audience_drift_guard_version": audience_drift_guard_version,
        }

    def _resolve_server_lineage(
        self,
        *,
        data: EditorialIdeaCandidateCreate,
        run: EditorialResearchRun,
        launch_policy: FirstChannelLaunchPolicyVersion,
        launch_run: LaunchRun,
        expected_phase: str,
        published_count: int,
    ) -> StrategicLineageV2:
        """Derive candidate strategy from active launch authority, not payload."""

        policy = self.session.get(CompiledChannelPolicySnapshot, run.policy_snapshot_id)
        if (
            policy is None
            or policy.channel_workspace_id != run.channel_workspace_id
            or policy.channel_profile_version_id != run.channel_profile_version_id
            or policy.status not in {"approved", "active"}
        ):
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_POLICY_AUTHORITY_MISMATCH"
            )
        existing_candidate_count = int(
            self.session.scalar(
                select(func.count(EditorialIdeaCandidate.id)).where(
                    EditorialIdeaCandidate.channel_workspace_id
                    == run.channel_workspace_id,
                    EditorialIdeaCandidate.active_launch_policy_version_id
                    == launch_policy.id,
                    EditorialIdeaCandidate.active_launch_run_id == launch_run.id,
                )
            )
            or 0
        )
        first_launch_candidate = published_count == 0 and existing_candidate_count == 0
        if first_launch_candidate:
            intent = StrategicIntent.ACQUISITION
            primary_variable = "audience_promise_validation"
            criteria_key = "AUDIENCE_PROMISE_VALIDATION"
            hypothesis = (
                "The approved audience promise can acquire the declared target "
                "audience through one bounded launch candidate."
            )
        elif expected_phase == "AUDIENCE_PROMISE":
            intent = StrategicIntent.AUDIENCE_DEPTH
            primary_variable = "audience_problem_depth"
            criteria_key = "AUDIENCE_PROBLEM_DEPTH"
            hypothesis = (
                "A deeper, policy-aligned audience problem explanation can improve "
                "qualified viewer relevance without changing the frozen promise."
            )
        elif expected_phase == "SERIES_PACKAGING":
            intent = StrategicIntent.SERIES_CONTINUITY
            primary_variable = "series_continuity"
            criteria_key = "SERIES_CONTINUITY"
            hypothesis = (
                "A bounded series continuation can strengthen return-viewer context "
                "while preserving the approved audience promise."
            )
        elif expected_phase == "ALLOCATION_PREPARATION":
            intent = StrategicIntent.CONTROLLED_EXPERIMENT
            primary_variable = "controlled_allocation"
            criteria_key = "CONTROLLED_ALLOCATION"
            hypothesis = (
                "One reversible allocation variable can be measured without changing "
                "the channel promise or launch policy."
            )
        else:
            intent = StrategicIntent.AUTHORITY
            primary_variable = "evidence_backed_authority"
            criteria_key = "EVIDENCE_BACKED_AUTHORITY"
            hypothesis = (
                "Evidence-backed instruction can build channel authority for the "
                "approved target audience."
            )
        reversibility = DecisionReversibility.TWO_WAY_DOOR
        criteria = {
            "criterion": criteria_key,
            "launch_policy_hash": launch_policy.canonical_hash,
            "measurement_scope": "LAUNCH_CANDIDATE",
            "experiment_phase": expected_phase,
            "first_launch_candidate": first_launch_candidate,
        }
        criteria_version = "launch-candidate-strategy-v1"
        lineage = {
            **self._audience_authority(policy=policy),
            "strategic_intent": intent,
            "intent_success_criteria": criteria,
            "intent_success_criteria_version": criteria_version,
            "intent_success_criteria_hash": (
                StrategicLineageV2.calculate_intent_success_criteria_hash(
                    strategic_intent=intent,
                    intent_success_criteria=criteria,
                    intent_success_criteria_version=criteria_version,
                    experiment_hypothesis=hypothesis,
                    primary_variable_under_test=primary_variable,
                    decision_reversibility=reversibility,
                )
            ),
            "experiment_hypothesis": hypothesis,
            "primary_variable_under_test": primary_variable,
            "decision_reversibility": reversibility,
            "active_launch_policy_version_id": launch_policy.id,
            "active_launch_policy_hash": launch_policy.canonical_hash,
            "active_launch_run_id": launch_run.id,
            "active_launch_run_hash": self._launch_run_authority_hash(
                launch_policy=launch_policy,
                launch_run=launch_run,
            ),
        }
        try:
            resolved = StrategicLineageV2.model_validate(lineage)
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_STRATEGIC_LINEAGE_INVALID"
            ) from exc
        claimed = data.model_dump(
            mode="python",
            include=set(resolved.model_fields),
            exclude_none=True,
        )
        expected = resolved.model_dump(mode="python")
        if any(expected[key] != value for key, value in claimed.items()):
            raise ValidationFailureError(
                "EDITORIAL_CANDIDATE_STRATEGIC_LINEAGE_MISMATCH"
            )
        return resolved

    def complete_run(
        self, *, run_id: uuid.UUID, actor: ActorContext
    ) -> EditorialResearchRun:
        run = self._locked_run(run_id)
        self._authorize(run, actor)
        if run.status == "COMPLETED":
            return run
        if run.status not in {"PENDING", "RUNNING"}:
            raise ConflictError(f"research run cannot complete from {run.status}")
        run.status = "COMPLETED"
        run.started_at = run.started_at or utc_now()
        run.completed_at = utc_now()
        run.reason_codes = ["EDITORIAL_RESEARCH_COMPLETED"]
        self.session.flush()
        return run

    def block_run(
        self,
        *,
        run_id: uuid.UUID,
        reason_codes: list[str],
        actor: ActorContext,
    ) -> EditorialResearchRun:
        """Persist a terminal, operator-visible safe stop for a research run."""

        run = self._locked_run(run_id)
        self._authorize(run, actor)
        if run.status == "BLOCKED":
            return run
        if run.status not in {"PENDING", "RUNNING"}:
            raise ConflictError(f"research run cannot block from {run.status}")
        run.status = "BLOCKED"
        run.started_at = run.started_at or utc_now()
        run.completed_at = utc_now()
        run.reason_codes = list(dict.fromkeys(reason_codes))
        self.session.flush()
        return run

    def attach_context_snapshots(
        self,
        *,
        run_id: uuid.UUID,
        context_pack_snapshot_id: uuid.UUID,
        channel_state_pack_snapshot_id: uuid.UUID,
        actor: ActorContext,
    ) -> EditorialResearchRun:
        """Bind immutable research context once, refusing cross-scope rewrites."""

        from app.db.models.m5 import ChannelStatePackSnapshot, ContextPackSnapshot

        run = self._locked_run(run_id)
        self._authorize(run, actor)
        context = self.session.get(ContextPackSnapshot, context_pack_snapshot_id)
        state = self.session.get(ChannelStatePackSnapshot, channel_state_pack_snapshot_id)
        if (
            context is None
            or state is None
            or context.company_id != run.company_id
            or context.channel_workspace_id != run.channel_workspace_id
            or context.channel_profile_version_id != run.channel_profile_version_id
            or context.policy_snapshot_id != run.policy_snapshot_id
            or state.company_id != run.company_id
            or state.channel_workspace_id != run.channel_workspace_id
            or state.policy_snapshot_id != run.policy_snapshot_id
            or state.context_pack_snapshot_id != context.id
            or (
                state.editorial_research_run_id is not None
                and state.editorial_research_run_id != run.id
            )
        ):
            raise ValidationFailureError("EDITORIAL_RESEARCH_CONTEXT_SCOPE_MISMATCH")
        if (
            run.context_pack_snapshot_id is not None
            and run.context_pack_snapshot_id != context.id
        ) or (
            run.channel_state_pack_snapshot_id is not None
            and run.channel_state_pack_snapshot_id != state.id
        ):
            raise ConflictError("EDITORIAL_RESEARCH_CONTEXT_ALREADY_FROZEN")
        run.context_pack_snapshot_id = context.id
        run.channel_state_pack_snapshot_id = state.id
        state.editorial_research_run_id = run.id
        self.session.flush()
        return run

    def transition_candidate(
        self,
        *,
        candidate_id: uuid.UUID,
        data: EditorialIdeaCandidateTransition,
        actor: ActorContext,
    ) -> EditorialIdeaCandidate:
        candidate = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None:
            raise NotFoundError(f"editorial candidate not found: {candidate_id}")
        run = self.session.get(
            EditorialResearchRun, candidate.editorial_research_run_id
        )
        if run is None:
            raise ValidationFailureError("EDITORIAL_RESEARCH_RUN_MISSING")
        self._authorize(run, actor)
        target = data.target_stage
        if target == candidate.stage:
            return candidate
        if target not in _CANDIDATE_TRANSITIONS[candidate.stage]:
            raise ConflictError(
                f"invalid candidate transition: {candidate.stage}->{target}"
            )
        preflight = (
            self.session.get(IdeaMarketPreflight, data.idea_market_preflight_id)
            if data.idea_market_preflight_id
            else None
        )
        if target in {"PREFLIGHT_PASS", "PREFLIGHT_BLOCK", "GREENLIT"}:
            if (
                preflight is None
                or preflight.editorial_idea_candidate_id != candidate.id
                or preflight.company_id != candidate.company_id
                or preflight.channel_workspace_id != candidate.channel_workspace_id
            ):
                raise ValidationFailureError(
                    "EDITORIAL_CANDIDATE_PREFLIGHT_AUTHORITY_MISMATCH"
                )
        if target == "PREFLIGHT_PASS" and (
            preflight.decision != "PASS"
            or preflight.policy_fit_state != "PASS"
            or not preflight.niche_contract_digest_hash
            or not preflight.target_market_digest_hash
            or (preflight.evidence_blob or {}).get("canonical_authority_verified")
            is not True
        ):
            raise ValidationFailureError("STRICT_EDITORIAL_PREFLIGHT_NOT_PASS")
        if target == "PREFLIGHT_BLOCK" and preflight.decision != "BLOCK":
            raise ValidationFailureError("EDITORIAL_PREFLIGHT_NOT_BLOCKED")
        canonical_evidence_refs = (
            (preflight.evidence_blob or {}).get("claim_evidence_refs")
            if preflight is not None
            else None
        )
        if target == "PREFLIGHT_PASS" and (
            not isinstance(canonical_evidence_refs, list)
            or not canonical_evidence_refs
            or any(
                not isinstance(item, dict)
                or item.get("type") != "search_demand_evidence"
                or not item.get("id")
                for item in canonical_evidence_refs
            )
        ):
                raise ValidationFailureError("EDITORIAL_CANONICAL_EVIDENCE_REQUIRED")
        if target == "PREFLIGHT_PASS" and (
            (preflight.evidence_blob or {}).get("demand_state")
            not in {"PASS", "EXPERIMENT_AUTHORIZED"}
        ):
            raise ValidationFailureError("EDITORIAL_MARKET_DEMAND_AUTHORITY_REQUIRED")
        if target == "GREENLIT":
            if candidate.stage != "PREFLIGHT_PASS":
                raise ValidationFailureError("GREENLIGHT_REQUIRES_PREFLIGHT_PASS")
            if (
                candidate.rights_policy_state != "PASS"
                or candidate.quality_state != "PASS"
                or not candidate.evidence_refs
            ):
                raise ValidationFailureError(
                    "GREENLIGHT_DETERMINISTIC_ELIGIBILITY_NOT_MET"
                )
        if target == "PREFLIGHT_PASS":
            candidate.evidence_refs = list(canonical_evidence_refs)
            candidate.rights_policy_state = "PASS"
            candidate.quality_state = "PASS"
            candidate.budget_readiness = "UNKNOWN"
        elif target == "PREFLIGHT_BLOCK":
            candidate.quality_state = "BLOCK"
            candidate.budget_readiness = "UNKNOWN"
        candidate.stage = target
        candidate.reason_codes = data.reason_codes
        self.session.flush()
        return candidate

    def _locked_run(self, run_id: uuid.UUID) -> EditorialResearchRun:
        record = self.session.scalar(
            select(EditorialResearchRun)
            .where(EditorialResearchRun.id == run_id)
            .with_for_update()
        )
        if record is None:
            raise NotFoundError(f"editorial research run not found: {run_id}")
        return record

    def _authorize(self, run: EditorialResearchRun, actor: ActorContext) -> None:
        self._authorize_company(company_id=run.company_id, actor=actor)

    def _authorize_company(self, *, company_id: uuid.UUID, actor: ActorContext) -> None:
        if actor.actor_type == ActorType.HUMAN_USER:
            require_company_permission(
                self.session,
                actor=actor,
                permission="editorial.manage",
                company_id=company_id,
            )
            return
        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or not actor.has_permission("editorial.manage")
        ):
            raise ValidationFailureError("EDITORIAL_RESEARCH_SYSTEM_WORKER_UNTRUSTED")

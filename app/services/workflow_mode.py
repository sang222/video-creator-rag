from app.core.enums import MaturityStage, WorkflowMode
from app.schemas.api import WorkflowModeResult


class WorkflowModeRouter:
    def route(self, workspace_context: dict, requested_mode: str | None = None) -> WorkflowModeResult:
        maturity = workspace_context["maturity_stage"]
        baseline_confidence = workspace_context["baseline_confidence"]
        budget_context = workspace_context.get("budget", {})
        reasons: list[str] = []

        if requested_mode == WorkflowMode.FULL_AI_CINEMATIC.value:
            return WorkflowModeResult(
                selected_mode=WorkflowMode.FULL_AI_CINEMATIC.value,
                reason=["explicitly requested; disabled by default so human preapproval is required"],
                budget={"target": 25.0, "hard_max": 50.0},
                media_policy={"max_new_ai_video_scenes": 99, "reuse_first": False, "fallback": "none"},
                requires_human_preapproval=True,
                alternative_modes=[WorkflowMode.PREMIUM_HYBRID.value, WorkflowMode.NORMAL_REUSE_FIRST.value],
            )

        if maturity == MaturityStage.NEW_CHANNEL.value or baseline_confidence < 0.3:
            reasons.append("new or low-confidence workspace must validate monetization path before scaling spend")
            return WorkflowModeResult(
                selected_mode=WorkflowMode.MONETIZATION_VALIDATION_MODE.value,
                reason=reasons,
                budget={
                    "target": float(budget_context.get("cost_per_video_target", 1.0)),
                    "hard_max": min(float(budget_context.get("hard_max_per_video", 2.5)), 2.5),
                },
                media_policy={"max_new_ai_video_scenes": 0, "reuse_first": True, "fallback": "image_motion"},
                requires_human_preapproval=False,
                alternative_modes=[WorkflowMode.FAST_TRACK.value, WorkflowMode.NORMAL_REUSE_FIRST.value],
            )

        if requested_mode == WorkflowMode.FAST_TRACK.value:
            reasons.append("urgent/trend mode requested")
            return WorkflowModeResult(
                selected_mode=WorkflowMode.FAST_TRACK.value,
                reason=reasons,
                budget={"target": 1.5, "hard_max": 2.5},
                media_policy={"max_new_ai_video_scenes": 0, "reuse_first": True, "fallback": "image_motion"},
                requires_human_preapproval=False,
                alternative_modes=[WorkflowMode.NORMAL_REUSE_FIRST.value],
            )

        if maturity in {MaturityStage.SCALED_CHANNEL.value, MaturityStage.MATURE_BRAND_CHANNEL.value}:
            reasons.append("scaled workspace can consider higher ROI hybrid production with preapproval guard")
            return WorkflowModeResult(
                selected_mode=WorkflowMode.PREMIUM_HYBRID.value,
                reason=reasons,
                budget={"target": 8.0, "hard_max": 12.0},
                media_policy={"max_new_ai_video_scenes": 6, "reuse_first": True, "fallback": "hybrid_generation"},
                requires_human_preapproval=True,
                alternative_modes=[WorkflowMode.NORMAL_REUSE_FIRST.value],
            )

        reasons.append("stable default reuse-first mode")
        return WorkflowModeResult(
            selected_mode=WorkflowMode.NORMAL_REUSE_FIRST.value,
            reason=reasons,
            budget={"target": 3.5, "hard_max": 5.5},
            media_policy={"max_new_ai_video_scenes": 2, "reuse_first": True, "fallback": "image_motion"},
            requires_human_preapproval=False,
            alternative_modes=[WorkflowMode.FAST_TRACK.value],
        )


from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.contracts.m12 import (
    IntegrationReadinessRead,
    ProviderBudgetCardRead,
    ProviderReadinessCheckRead,
    ProviderReadinessSnapshotRead,
    ProviderSmokeRequest,
    ProviderSummaryRead,
    RealSmokeRunRead,
)
from app.core.config import Settings, VEO_ALLOWED_DURATION_SECONDS, VEO_GA_MODEL_ID, VEO_MAX_DURATION_SECONDS, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    GoogleDriveMediaCredential,
    ProviderReadinessCheck,
    ProviderReadinessSnapshot,
    RealSmokeRun,
    YouTubeMonitoringCredential,
)
from app.providers.google_vertex_veo import GoogleVertexVeoExecutionConfig, GoogleVertexVeoProvider, GoogleVertexVeoRequest
from app.providers.ollama import OllamaLLMProvider
from app.services.m10_1 import LLMRouterConfigLoader, LLMRouterService
from app.services.m10_2 import GoogleVertexVeoConfigService
from app.services.m10_3 import (
    YouTubeMonitoringConfigService,
    YouTubeOAuthCredentialService,
    YouTubeOwnerAnalyticsProvider,
    YouTubePublicStatsProvider,
)
from app.services.m10_5 import GoogleDriveConfigService, GoogleDriveOAuthCredentialService, GoogleDriveUploadService


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_OLLAMA_SMOKE_LANES = ("cheap_structured", "long_context_text", "visual_creative_review", "gatekeeper_soft_review")
REQUIRED_YOUTUBE_SCOPES = {
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
}
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
PROVIDER_ORDER = (
    "ollama",
    "youtube-public",
    "youtube-owner",
    "google-drive",
    "google-vertex-veo",
    "elevenlabs",
    "creatomate",
    "cloud-final-renderer",
)
SECRET_KEY_FRAGMENTS = ("secret", "token", "api_key", "apikey", "password", "private", "credential", "authorization")
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "ya29.", "ghp_", "xoxb-", "client_secret")
BUDGET_NOTE = "Đây là budget cấu hình cứng từ env, chưa phải chi phí thực tế đã tiêu."


@dataclass(frozen=True)
class ProviderCheckDraft:
    provider_key: str
    provider_type: str
    check_type: str
    check_state: str
    operator_summary: str
    next_action: str | None = None
    reason_codes: tuple[str, ...] = ()
    technical_appendix: dict[str, Any] | None = None


class SecurityRedactionService:
    def redact_value(self, key: str, value: Any) -> Any:
        lowered = key.lower()
        if any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS):
            return _redacted_presence(value)
        if isinstance(value, str) and any(marker in value for marker in RAW_SECRET_MARKERS):
            return "[REDACTED]"
        if isinstance(value, str) and ("service_account" in value.lower() or value.endswith(".json")):
            return "[REDACTED_PATH]" if value else None
        return value

    def redact_dict(self, payload: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                redacted[key] = self.redact_dict(value)
            elif isinstance(value, list):
                redacted[key] = [self.redact_dict(item) if isinstance(item, dict) else self.redact_value(key, item) for item in value]
            else:
                redacted[key] = self.redact_value(key, value)
        return redacted

    def safe_error(self, message: str | None) -> str | None:
        if message is None:
            return None
        redacted = message
        for marker in RAW_SECRET_MARKERS:
            redacted = redacted.replace(marker, "[REDACTED]")
        return redacted[:500]


class EnvConfigAuditService:
    def __init__(self, settings: Settings | None = None, redactor: SecurityRedactionService | None = None):
        self.settings = settings or get_settings()
        self.redactor = redactor or SecurityRedactionService()

    def secret_configured(self, value: Any) -> bool:
        if value is None:
            return False
        getter = getattr(value, "get_secret_value", None)
        raw = getter() if callable(getter) else value
        return bool(raw)

    def string_configured(self, value: str | None) -> bool:
        return bool(value and value.strip())

    def money(self, value: Decimal | int | float | str | None) -> str | None:
        if value is None:
            return None
        amount = Decimal(str(value))
        text = format(amount, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return f"${text or '0'} USD"

    def integer(self, value: int | None, unit: str) -> str | None:
        if value is None:
            return None
        return f"{value:,} {unit}"

    def budget_cards(self, provider_summaries: list[ProviderSummaryRead]) -> list[ProviderBudgetCardRead]:
        readiness = {summary.provider_key: summary.readiness_state for summary in provider_summaries}
        settings = self.settings
        return [
            self._budget_card(
                key="total-ai",
                provider_name="Tổng budget AI",
                role="Giới hạn tổng AI hard-env",
                configured_plan=settings.budget_mode,
                configured_monthly_cap=self.money(settings.monthly_ai_budget_usd),
                budget_basis="hard_env",
                readiness_state="PASS" if settings.budget_mode == "hard_env" and settings.monthly_ai_budget_usd is not None else "WARNING",
                missing_env_keys=_missing(
                    ("VCOS_BUDGET_MODE", settings.budget_mode == "hard_env"),
                    ("VCOS_MONTHLY_AI_BUDGET_USD", settings.monthly_ai_budget_usd is not None),
                ),
            ),
            self._budget_card(
                key="ollama-llm",
                provider_name="Ollama LLMRouter",
                role="LLM script/review/router lanes",
                configured_plan=settings.llm_budget_note,
                configured_monthly_cap=self.money(settings.llm_monthly_budget_usd),
                budget_basis="hard_env_usd",
                readiness_state=readiness.get("ollama", "UNKNOWN"),
                missing_env_keys=_missing(("VCOS_LLM_MONTHLY_BUDGET_USD", settings.llm_monthly_budget_usd is not None)),
            ),
            self._budget_card(
                key="elevenlabs",
                provider_name="ElevenLabs",
                role="Voice-only provider",
                configured_plan=settings.elevenlabs_plan,
                configured_monthly_cap=self.money(settings.elevenlabs_monthly_cap_usd),
                budget_basis=settings.elevenlabs_budget_basis or "credits/characters",
                readiness_state=readiness.get("elevenlabs", "UNKNOWN"),
                missing_env_keys=_missing(
                    ("VCOS_ELEVENLABS_PLAN", self.string_configured(settings.elevenlabs_plan)),
                    ("VCOS_ELEVENLABS_MONTHLY_CAP_USD", settings.elevenlabs_monthly_cap_usd is not None),
                    ("VCOS_ELEVENLABS_MONTHLY_CREDIT_CAP", settings.elevenlabs_monthly_credit_cap is not None),
                    ("VCOS_ELEVENLABS_BUDGET_BASIS", self.string_configured(settings.elevenlabs_budget_basis)),
                ),
                appendix={"credit_cap": self.integer(settings.elevenlabs_monthly_credit_cap, "credits/chars")},
            ),
            self._budget_card(
                key="google-vertex-veo",
                provider_name="Google Vertex Veo",
                role="AI hero video-only",
                configured_plan=settings.veo_model_id or VEO_GA_MODEL_ID,
                configured_monthly_cap=self.money(settings.veo_monthly_budget_usd),
                budget_basis=f"{self.money(settings.veo_cost_per_second_1080p_video_only) or 'Chưa cấu hình'} / giây 1080p",
                readiness_state=readiness.get("google-vertex-veo", "UNKNOWN"),
                missing_env_keys=_missing(
                    ("VCOS_AI_HERO_PROVIDER", _normalized(settings.ai_hero_provider) == "google_vertex_veo"),
                    ("VCOS_VEO_MONTHLY_CAP_USD", settings.veo_monthly_budget_usd is not None),
                    ("VCOS_VEO_COST_PER_SECOND_1080P", settings.veo_cost_per_second_1080p_video_only is not None),
                    ("VCOS_VEO_ALLOWED_DURATIONS", True),
                    ("VCOS_VEO_MAX_DURATION_SECONDS", settings.veo_max_duration_seconds is not None),
                ),
                appendix={
                    "allowed_durations": list(VEO_ALLOWED_DURATION_SECONDS),
                    "default_duration_seconds": settings.veo_default_duration_seconds,
                    "max_duration_seconds": settings.veo_max_duration_seconds,
                },
            ),
            self._budget_card(
                key="creatomate",
                provider_name="Creatomate",
                role="Shorts/cards/thumbnails; không phải renderer ráp video dài",
                configured_plan=settings.creatomate_plan,
                configured_monthly_cap=self.money(settings.creatomate_monthly_budget_usd),
                budget_basis="credits/renders",
                readiness_state=readiness.get("creatomate", "UNKNOWN"),
                missing_env_keys=_missing(
                    ("CREATOMATE_PLAN", self.string_configured(settings.creatomate_plan)),
                    ("CREATOMATE_MONTHLY_CREDITS", settings.creatomate_monthly_credits is not None),
                    ("CREATOMATE_MONTHLY_BUDGET_USD", settings.creatomate_monthly_budget_usd is not None),
                ),
                appendix={"monthly_credits": settings.creatomate_monthly_credits},
            ),
            self._budget_card(
                key="optional-spend-disabled",
                provider_name="Stock / Music / Extra AI Image",
                role="Optional spend disabled",
                configured_plan="disabled",
                configured_monthly_cap="$0 USD",
                budget_basis="hard_env_zero",
                readiness_state="PASS"
                if settings.stock_monthly_budget_usd == 0
                and settings.music_sfx_monthly_budget_usd == 0
                and settings.extra_ai_image_monthly_budget_usd == 0
                else "WARNING",
                missing_env_keys=_missing(
                    ("VCOS_STOCK_MONTHLY_BUDGET_USD", settings.stock_monthly_budget_usd is not None),
                    ("VCOS_MUSIC_SFX_MONTHLY_BUDGET_USD", settings.music_sfx_monthly_budget_usd is not None),
                    ("VCOS_EXTRA_AI_IMAGE_MONTHLY_BUDGET_USD", settings.extra_ai_image_monthly_budget_usd is not None),
                ),
            ),
        ]

    def _budget_card(
        self,
        *,
        key: str,
        provider_name: str,
        role: str,
        configured_plan: str | None,
        configured_monthly_cap: str | None,
        budget_basis: str,
        readiness_state: str,
        missing_env_keys: list[str],
        appendix: dict[str, Any] | None = None,
    ) -> ProviderBudgetCardRead:
        return ProviderBudgetCardRead(
            key=key,
            provider_name=provider_name,
            role=role,
            configured_plan=configured_plan,
            configured_monthly_cap=configured_monthly_cap,
            budget_basis=budget_basis,
            readiness_state=readiness_state,  # type: ignore[arg-type]
            missing_env_keys=missing_env_keys,
            note=BUDGET_NOTE,
            technical_appendix=self.redactor.redact_dict({"no_actual_spend_calculation": True, **(appendix or {})}),
        )


class ProviderNextActionService:
    def for_checks(self, provider_key: str, checks: list[ProviderCheckDraft]) -> str:
        for state in ("BLOCKED", "FAILED", "WARNING", "UNKNOWN", "SKIPPED"):
            for check in checks:
                if check.check_state == state and check.next_action:
                    return check.next_action
        return "Không cần thao tác thêm trước khi chạy bước readiness tiếp theo."


class _BaseReadinessCheck:
    provider_key: str
    provider_name: str
    provider_type: str

    def __init__(self, session: Session, settings: Settings | None = None, redactor: SecurityRedactionService | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.redactor = redactor or SecurityRedactionService()
        self.env = EnvConfigAuditService(self.settings, self.redactor)

    def evaluate(self) -> list[ProviderCheckDraft]:
        raise NotImplementedError

    def summary(self, checks: list[ProviderCheckDraft]) -> ProviderSummaryRead:
        state = _provider_state(checks)
        return ProviderSummaryRead(
            provider_key=self.provider_key,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            readiness_state=state,  # type: ignore[arg-type]
            status_label=_status_label(state),
            operator_summary=_summary_for_state(self.provider_name, state),
            next_action=ProviderNextActionService().for_checks(self.provider_key, checks),
            smoke_state=_smoke_state(checks),
            safe_config=self.safe_config(),
            missing_env_keys=sorted(set(_missing_from_checks(checks))),
            reason_codes=sorted(set(reason for check in checks for reason in check.reason_codes)),
            technical_appendix={"check_count": len(checks), "raw_status_in_appendix_only": True},
        )

    def safe_config(self) -> dict[str, Any]:
        return {}

    def _check(
        self,
        check_type: str,
        check_state: str,
        operator_summary: str,
        *,
        next_action: str | None = None,
        reason_codes: tuple[str, ...] = (),
        technical_appendix: dict[str, Any] | None = None,
    ) -> ProviderCheckDraft:
        return ProviderCheckDraft(
            provider_key=self.provider_key,
            provider_type=self.provider_type,
            check_type=check_type,
            check_state=check_state,
            operator_summary=operator_summary,
            next_action=next_action,
            reason_codes=reason_codes,
            technical_appendix=self.redactor.redact_dict(technical_appendix or {}),
        )


class OllamaReadinessCheck(_BaseReadinessCheck):
    provider_key = "ollama"
    provider_name = "Ollama Router"
    provider_type = "LLM_SCRIPT_ENGINE"

    def evaluate(self) -> list[ProviderCheckDraft]:
        checks: list[ProviderCheckDraft] = []
        provider_ok = self.settings.llm_provider.lower() == "ollama"
        checks.append(
            self._check(
                "CONFIG",
                "PASS" if provider_ok and self.settings.ollama_base_url else "BLOCKED",
                "Ollama Router dùng provider ollama và base URL đã có." if provider_ok else "LLM provider không phải Ollama.",
                next_action=None if provider_ok else "Đặt VCOS_LLM_PROVIDER=ollama.",
                reason_codes=("OLLAMA_CONFIG_READY",) if provider_ok else ("OLLAMA_PROVIDER_NOT_SELECTED",),
                technical_appendix={
                    "OLLAMA_BASE_URL": self.settings.ollama_base_url,
                    "VCOS_LLM_PROVIDER": self.settings.llm_provider,
                    "VCOS_LLM_REAL_EXECUTION_ENABLED": self.settings.llm_real_execution_enabled,
                },
            )
        )
        lanes = LLMRouterConfigLoader(self.session).list_lanes(profile_key="default")
        lane_names = {lane.lane_name for lane in lanes}
        missing_lanes = [lane for lane in REQUIRED_OLLAMA_SMOKE_LANES if lane not in lane_names]
        configured_models = _ollama_models_from_lanes(lanes)
        glm_models = [model for model in configured_models if "glm" in model.lower()]
        checks.append(
            self._check(
                "CAPABILITY",
                "PASS" if not missing_lanes else "BLOCKED",
                "Các lane smoke bắt buộc đã có trong LLMRouter." if not missing_lanes else "Thiếu lane smoke bắt buộc trong LLMRouter.",
                next_action=None if not missing_lanes else "Chạy lại config/seed LLMRouter M10.1.",
                reason_codes=("OLLAMA_LANES_READY",) if not missing_lanes else ("OLLAMA_LANE_MISSING",),
                technical_appendix={"available_lanes": sorted(lane_names), "missing_lanes": missing_lanes, "model_count": len(configured_models)},
            )
        )
        checks.append(
            self._check(
                "SECURITY",
                "PASS" if not glm_models else "BLOCKED",
                "Không có GLM trong lane/model Ollama." if not glm_models else "Phát hiện model GLM bị cấm.",
                next_action=None if not glm_models else "Gỡ model GLM khỏi lane router.",
                reason_codes=("NO_GLM_MODEL_CONFIGURED",) if not glm_models else ("GLM_MODEL_FORBIDDEN",),
                technical_appendix={"glm_model_count": len(glm_models)},
            )
        )
        smoke_enabled = self.settings.llm_router_real_smoke and self.settings.llm_real_execution_enabled
        checks.append(
            self._check(
                "REAL_SMOKE",
                "SKIPPED" if not smoke_enabled else "UNKNOWN",
                "Ollama real smoke đang tắt theo env guard." if not smoke_enabled else "Ollama real smoke đã bật; chạy endpoint smoke để kiểm tra model.",
                next_action="Chỉ bật VCOS_LLM_ROUTER_REAL_SMOKE=true và VCOS_LLM_REAL_EXECUTION_ENABLED=true khi muốn gọi Ollama thật."
                if not smoke_enabled
                else "Chạy smoke Ollama để xác minh model callable.",
                reason_codes=("OLLAMA_REAL_SMOKE_SKIPPED",) if not smoke_enabled else ("OLLAMA_REAL_SMOKE_READY_TO_RUN",),
                technical_appendix={"real_smoke_enabled": smoke_enabled, "no_provider_call_in_readiness_read": True},
            )
        )
        return checks

    def safe_config(self) -> dict[str, Any]:
        return {
            "base_url": self.settings.ollama_base_url,
            "provider": self.settings.llm_provider,
            "real_execution_enabled": self.settings.llm_real_execution_enabled,
            "router_real_smoke": self.settings.llm_router_real_smoke,
        }


class YouTubePublicReadinessCheck(_BaseReadinessCheck):
    provider_key = "youtube-public"
    provider_name = "YouTube Public Monitor"
    provider_type = "YOUTUBE_DATA_API"

    def evaluate(self) -> list[ProviderCheckDraft]:
        enabled = self.settings.youtube_public_monitor_enabled
        key_configured = self.env.secret_configured(self.settings.youtube_data_api_key)
        test_video_configured = self.env.string_configured(self.settings.youtube_test_video_id)
        return [
            self._check(
                "CONFIG",
                "PASS" if enabled else "WARNING",
                "YouTube public monitor đã bật." if enabled else "YouTube public monitor đang tắt.",
                next_action=None if enabled else "Đặt YOUTUBE_PUBLIC_MONITOR_ENABLED=true khi muốn theo dõi public stats.",
                reason_codes=("YOUTUBE_PUBLIC_ENABLED",) if enabled else ("YOUTUBE_PUBLIC_DISABLED",),
                technical_appendix={"YOUTUBE_PUBLIC_MONITOR_ENABLED": enabled},
            ),
            self._check(
                "CREDENTIAL",
                "PASS" if key_configured else "BLOCKED",
                "YouTube Data API key đã cấu hình." if key_configured else "Thiếu YouTube Data API key.",
                next_action=None if key_configured else "Thêm YOUTUBE_DATA_API_KEY vào .env/secret manager.",
                reason_codes=("YOUTUBE_DATA_API_KEY_CONFIGURED",) if key_configured else ("YOUTUBE_DATA_API_KEY_MISSING",),
                technical_appendix={"YOUTUBE_DATA_API_KEY": _redacted_presence(key_configured), "missing_env_keys": [] if key_configured else ["YOUTUBE_DATA_API_KEY"]},
            ),
            self._check(
                "REAL_SMOKE",
                "SKIPPED",
                "Public stats smoke mặc định bỏ qua; zero/unknown/unavailable chỉ được xác minh khi bật smoke thật.",
                next_action="Đặt VCOS_YOUTUBE_REAL_PUBLIC_SMOKE=true và YOUTUBE_TEST_VIDEO_ID để chạy smoke an toàn."
                if not test_video_configured
                else "Bật VCOS_YOUTUBE_REAL_PUBLIC_SMOKE=true nếu muốn gọi YouTube Data API thật.",
                reason_codes=("YOUTUBE_PUBLIC_SMOKE_SKIPPED",),
                technical_appendix={"VCOS_YOUTUBE_REAL_PUBLIC_SMOKE": self.settings.youtube_real_public_smoke, "YOUTUBE_TEST_VIDEO_ID_CONFIGURED": test_video_configured},
            ),
        ]

    def safe_config(self) -> dict[str, Any]:
        return {
            "public_monitor_enabled": self.settings.youtube_public_monitor_enabled,
            "api_key_configured": self.env.secret_configured(self.settings.youtube_data_api_key),
            "test_video_id_configured": self.env.string_configured(self.settings.youtube_test_video_id),
            "learning_authority": "WEAK",
        }


class YouTubeOwnerAnalyticsReadinessCheck(_BaseReadinessCheck):
    provider_key = "youtube-owner"
    provider_name = "YouTube Owner Analytics"
    provider_type = "YOUTUBE_ANALYTICS_API"

    def evaluate(self) -> list[ProviderCheckDraft]:
        enabled = self.settings.youtube_owner_analytics_enabled
        config = YouTubeMonitoringConfigService(self.settings)
        scopes = set(config.scopes)
        scopes_ok = REQUIRED_YOUTUBE_SCOPES.issubset(scopes)
        oauth_configured = config._oauth_client_config_or_none() is not None  # noqa: SLF001 - M12 read-only config audit.
        token = self._latest_token()
        connected = token is not None and token.connection_state == "CONNECTED"
        return [
            self._check(
                "CONFIG",
                "PASS" if enabled and oauth_configured else "WARNING",
                "YouTube owner analytics config đã có." if enabled and oauth_configured else "YouTube owner analytics thiếu config hoặc đang tắt.",
                next_action=None if enabled and oauth_configured else "Bật YOUTUBE_OWNER_ANALYTICS_ENABLED và cấu hình OAuth client JSON/id/secret.",
                reason_codes=("YOUTUBE_OWNER_CONFIG_READY",) if enabled and oauth_configured else ("YOUTUBE_OWNER_CONFIG_INCOMPLETE",),
                technical_appendix={
                    "YOUTUBE_OWNER_ANALYTICS_ENABLED": enabled,
                    "oauth_client_configured": oauth_configured,
                    "missing_env_keys": _missing(
                        ("YOUTUBE_OWNER_ANALYTICS_ENABLED", enabled),
                        ("YOUTUBE_OAUTH_CLIENT_SECRETS_FILE_OR_CLIENT_FIELDS", oauth_configured),
                    ),
                },
            ),
            self._check(
                "CREDENTIAL",
                "PASS" if connected else "BLOCKED",
                "YouTube OAuth token đã kết nối." if connected else "YouTube owner analytics cần OAuth token.",
                next_action=None if connected else "Bấm Kết nối YouTube để cấp quyền youtube.readonly và yt-analytics.readonly.",
                reason_codes=("YOUTUBE_OAUTH_TOKEN_CONNECTED",) if connected else ("YOUTUBE_OWNER_NEEDS_AUTH",),
                technical_appendix={"token_connected": connected, "credential_reference_present": token is not None},
            ),
            self._check(
                "SECURITY",
                "PASS" if scopes_ok else "BLOCKED",
                "Scopes YouTube đúng mức readonly." if scopes_ok else "Thiếu scope readonly bắt buộc.",
                next_action=None if scopes_ok else "Cấu hình scopes youtube.readonly và yt-analytics.readonly, không thêm upload scope.",
                reason_codes=("YOUTUBE_SCOPES_READY",) if scopes_ok else ("YOUTUBE_SCOPE_MISSING",),
                technical_appendix={"scopes": sorted(scopes), "required_scopes": sorted(REQUIRED_YOUTUBE_SCOPES)},
            ),
            self._check(
                "REAL_SMOKE",
                "SKIPPED",
                "Owner analytics smoke mặc định bỏ qua; thiếu token sẽ trả NEEDS_AUTH, không fake metric.",
                next_action="Bật VCOS_YOUTUBE_REAL_OWNER_SMOKE=true sau khi OAuth token đã kết nối.",
                reason_codes=("YOUTUBE_OWNER_SMOKE_SKIPPED",),
                technical_appendix={"VCOS_YOUTUBE_REAL_OWNER_SMOKE": self.settings.youtube_real_owner_smoke, "no_fake_metrics": True},
            ),
        ]

    def safe_config(self) -> dict[str, Any]:
        config = YouTubeMonitoringConfigService(self.settings)
        token = self._latest_token()
        return {
            "owner_analytics_enabled": self.settings.youtube_owner_analytics_enabled,
            "oauth_client_configured": config._oauth_client_config_or_none() is not None,  # noqa: SLF001
            "connected": token is not None and token.connection_state == "CONNECTED",
            "scopes": config.scopes,
            "learning_authority": "STRONG",
        }

    def _latest_token(self) -> YouTubeMonitoringCredential | None:
        return self.session.scalars(
            select(YouTubeMonitoringCredential)
            .where(YouTubeMonitoringCredential.provider_key == "YOUTUBE_ANALYTICS_API")
            .order_by(desc(YouTubeMonitoringCredential.updated_at))
            .limit(1)
        ).one_or_none()


class GoogleDriveReadinessCheck(_BaseReadinessCheck):
    provider_key = "google-drive"
    provider_name = "Google Drive"
    provider_type = "MEDIA_STORAGE"

    def evaluate(self) -> list[ProviderCheckDraft]:
        config = GoogleDriveConfigService(self.settings)
        offload_enabled = config.offload_enabled()
        oauth_configured = config.oauth_configured()
        root_configured = bool(config.root_folder_id())
        credential = self._latest_credential()
        connected = credential is not None and credential.connection_state == "CONNECTED"
        scopes = set(config.scopes if oauth_configured else [])
        scope_ok = DRIVE_SCOPE in scopes
        return [
            self._check(
                "CONFIG",
                "PASS" if offload_enabled and root_configured else "BLOCKED",
                "Google Drive offload và root folder đã cấu hình." if offload_enabled and root_configured else "Google Drive thiếu offload flag hoặc root folder.",
                next_action=None if offload_enabled and root_configured else "Bật GOOGLE_DRIVE_OFFLOAD_ENABLED và thêm GOOGLE_DRIVE_ROOT_FOLDER_ID.",
                reason_codes=("GOOGLE_DRIVE_CONFIG_READY",) if offload_enabled and root_configured else ("GOOGLE_DRIVE_CONFIG_MISSING",),
                technical_appendix={
                    "GOOGLE_DRIVE_OFFLOAD_ENABLED": offload_enabled,
                    "GOOGLE_DRIVE_ROOT_FOLDER_ID": _redacted_presence(root_configured),
                    "missing_env_keys": _missing(("GOOGLE_DRIVE_OFFLOAD_ENABLED", offload_enabled), ("GOOGLE_DRIVE_ROOT_FOLDER_ID", root_configured)),
                },
            ),
            self._check(
                "CREDENTIAL",
                "PASS" if oauth_configured and connected else "BLOCKED",
                "Google Drive OAuth token đã kết nối." if oauth_configured and connected else "Google Drive cần OAuth client/token.",
                next_action=None if oauth_configured and connected else "Bấm Kết nối Google Drive để cấp quyền drive.file.",
                reason_codes=("GOOGLE_DRIVE_CONNECTED",) if oauth_configured and connected else ("GOOGLE_DRIVE_NEEDS_AUTH",),
                technical_appendix={"oauth_client_configured": oauth_configured, "token_connected": connected},
            ),
            self._check(
                "SECURITY",
                "PASS" if scope_ok else "BLOCKED",
                "Google Drive chỉ dùng scope drive.file." if scope_ok else "Thiếu scope drive.file hoặc dùng scope rộng.",
                next_action=None if scope_ok else "Cấu hình GOOGLE_DRIVE_OAUTH_SCOPES=https://www.googleapis.com/auth/drive.file.",
                reason_codes=("GOOGLE_DRIVE_SCOPE_READY",) if scope_ok else ("GOOGLE_DRIVE_SCOPE_MISSING",),
                technical_appendix={"scopes": sorted(scopes), "required_scope": DRIVE_SCOPE},
            ),
            self._check(
                "REAL_SMOKE",
                "SKIPPED",
                "Drive upload smoke mặc định bỏ qua để tránh upload thật ngoài ý muốn.",
                next_action="Chỉ bật VCOS_DRIVE_REAL_UPLOAD_SMOKE=true trong test folder/root folder an toàn.",
                reason_codes=("GOOGLE_DRIVE_SMOKE_SKIPPED",),
                technical_appendix={"VCOS_DRIVE_REAL_UPLOAD_SMOKE": self.settings.drive_real_upload_smoke},
            ),
        ]

    def safe_config(self) -> dict[str, Any]:
        credential = self._latest_credential()
        return {
            "offload_enabled": self.settings.google_drive_offload_enabled,
            "connected": credential is not None and credential.connection_state == "CONNECTED",
            "root_folder_configured": bool(self.settings.google_drive_root_folder_id),
            "scope": DRIVE_SCOPE,
        }

    def _latest_credential(self) -> GoogleDriveMediaCredential | None:
        return self.session.scalars(select(GoogleDriveMediaCredential).order_by(desc(GoogleDriveMediaCredential.updated_at)).limit(1)).one_or_none()


class GoogleVertexVeoReadinessCheck(_BaseReadinessCheck):
    provider_key = "google-vertex-veo"
    provider_name = "Google Vertex Veo"
    provider_type = "AI_VIDEO_HERO_PROVIDER"

    def evaluate(self) -> list[ProviderCheckDraft]:
        try:
            config = GoogleVertexVeoConfigService(self.session).resolve()
            config_error = None
        except Exception as exc:
            config = None
            config_error = str(exc)
        configured = config is not None and config.model_id == VEO_GA_MODEL_ID and list(config.allowed_duration_seconds) == [Decimal("4"), Decimal("6"), Decimal("8")]
        env_missing = []
        if config and config.real_execution_enabled:
            env_missing = _missing(
                ("GOOGLE_CLOUD_PROJECT_ID", bool(config.project_id)),
                ("GOOGLE_CLOUD_LOCATION", bool(config.location)),
                ("GOOGLE_APPLICATION_CREDENTIALS", bool(config.service_account_path)),
            )
        provider_selected = _normalized(self.settings.ai_hero_provider) in {None, "google_vertex_veo"}
        audio_ok = config is not None and config.audio_enabled is False
        max_ok = config is not None and config.max_duration_seconds == Decimal(str(VEO_MAX_DURATION_SECONDS))
        return [
            self._check(
                "CONFIG",
                "PASS" if configured and provider_selected and audio_ok and max_ok else "BLOCKED",
                "Google Vertex Veo đã cấu hình đúng model/duration/audio guard." if configured and provider_selected and audio_ok and max_ok else "Veo config chưa đúng guard M10.4/M12.",
                next_action=None if configured and provider_selected and audio_ok and max_ok else "Đặt model veo-3.1-fast-generate-001, duration 4/6/8, max 8, audio=false.",
                reason_codes=("VEO_CONFIG_READY",) if configured and provider_selected and audio_ok and max_ok else ("VEO_CONFIG_MISSING_OR_INVALID",),
                technical_appendix={
                    "config_error": config_error,
                    "model_id": config.model_id if config else None,
                    "allowed_durations": [str(item) for item in config.allowed_duration_seconds] if config else [],
                    "max_duration_seconds": str(config.max_duration_seconds) if config else None,
                    "audio_enabled": config.audio_enabled if config else None,
                    "GOOGLE_APPLICATION_CREDENTIALS": _redacted_presence(config.service_account_path if config else None),
                    "missing_env_keys": env_missing,
                },
            ),
            self._check(
                "BUDGET",
                "PASS" if self.settings.veo_monthly_budget_usd is not None else "WARNING",
                "Veo monthly cap hard-env đã hiển thị." if self.settings.veo_monthly_budget_usd is not None else "Veo monthly cap chưa cấu hình trong env.",
                next_action=None if self.settings.veo_monthly_budget_usd is not None else "Thêm VCOS_VEO_MONTHLY_CAP_USD hoặc VCOS_VEO_MONTHLY_BUDGET_USD vào .env.",
                reason_codes=("VEO_HARD_ENV_BUDGET_CONFIGURED",) if self.settings.veo_monthly_budget_usd is not None else ("VEO_HARD_ENV_BUDGET_MISSING",),
                technical_appendix={"no_actual_usage_calculation": True},
            ),
            self._check(
                "REAL_SMOKE",
                "SKIPPED" if not (config and config.real_execution_enabled and config.real_smoke_enabled) else ("BLOCKED" if env_missing else "UNKNOWN"),
                "Veo smoke bị bỏ qua theo env guard." if not (config and config.real_execution_enabled and config.real_smoke_enabled) else "Veo real smoke đã bật; cần credential/project trước khi gọi.",
                next_action="Chỉ bật VCOS_VEO_REAL_EXECUTION_ENABLED=true và VCOS_VEO_REAL_SMOKE=true khi đã sẵn sàng chịu chi phí."
                if not (config and config.real_execution_enabled and config.real_smoke_enabled)
                else ("Bổ sung Google project/location/service account." if env_missing else "Chạy smoke Veo có guard."),
                reason_codes=("VEO_SMOKE_SKIPPED",) if not (config and config.real_execution_enabled and config.real_smoke_enabled) else ("VEO_REAL_EXECUTION_CONFIG_MISSING",) if env_missing else ("VEO_SMOKE_READY_TO_RUN",),
                technical_appendix={"real_execution_enabled": bool(config and config.real_execution_enabled), "real_smoke_enabled": bool(config and config.real_smoke_enabled)},
            ),
        ]

    def safe_config(self) -> dict[str, Any]:
        try:
            config = GoogleVertexVeoConfigService(self.session).resolve()
        except Exception:
            return {"configured": False, "service_account_path": "[REDACTED_PATH]"}
        return {
            "model_id": config.model_id,
            "duration_rules": "4,6,8; max 8s",
            "audio_enabled": config.audio_enabled,
            "project_configured": bool(config.project_id),
            "location_configured": bool(config.location),
            "service_account_path": _redacted_presence(config.service_account_path),
            "real_execution_enabled": config.real_execution_enabled,
            "real_smoke": config.real_smoke_enabled,
        }


class ElevenLabsReadinessCheck(_BaseReadinessCheck):
    provider_key = "elevenlabs"
    provider_name = "ElevenLabs"
    provider_type = "API_NATIVE_TTS"

    def evaluate(self) -> list[ProviderCheckDraft]:
        key_configured = self.env.secret_configured(self.settings.elevenlabs_api_key)
        budget_missing = _missing(
            ("VCOS_ELEVENLABS_PLAN", bool(self.settings.elevenlabs_plan)),
            ("VCOS_ELEVENLABS_MONTHLY_CAP_USD", self.settings.elevenlabs_monthly_cap_usd is not None),
            ("VCOS_ELEVENLABS_MONTHLY_CREDIT_CAP", self.settings.elevenlabs_monthly_credit_cap is not None),
            ("VCOS_ELEVENLABS_BUDGET_BASIS", bool(self.settings.elevenlabs_budget_basis)),
        )
        return [
            self._check(
                "CREDENTIAL",
                "PASS" if key_configured else "BLOCKED",
                "ElevenLabs API key đã cấu hình." if key_configured else "Thiếu ElevenLabs API key.",
                next_action=None if key_configured else "Thêm ELEVENLABS_API_KEY vào secret manager/.env.",
                reason_codes=("ELEVENLABS_API_KEY_CONFIGURED",) if key_configured else ("ELEVENLABS_API_KEY_MISSING",),
                technical_appendix={"ELEVENLABS_API_KEY": _redacted_presence(key_configured), "missing_env_keys": [] if key_configured else ["ELEVENLABS_API_KEY"]},
            ),
            self._check(
                "CAPABILITY",
                "PASS",
                "ElevenLabs chỉ được dùng vai trò voice-only trong M12.",
                reason_codes=("ELEVENLABS_VOICE_ONLY_ROLE",),
                technical_appendix={"no_paid_voice_generation_by_default": True, "provider_role": "voice-only"},
            ),
            self._check(
                "BUDGET",
                "PASS" if not budget_missing else "WARNING",
                "Budget ElevenLabs hard-env đã có." if not budget_missing else "Budget ElevenLabs hard-env chưa đủ.",
                next_action=None if not budget_missing else "Thêm plan/cap/credit/basis ElevenLabs vào .env.",
                reason_codes=("ELEVENLABS_BUDGET_CONFIGURED",) if not budget_missing else ("ELEVENLABS_BUDGET_MISSING",),
                technical_appendix={"missing_env_keys": budget_missing, "no_actual_spend_calculation": True},
            ),
            self._check(
                "REAL_SMOKE",
                "SKIPPED",
                "ElevenLabs smoke mặc định bỏ qua; M12 không sinh voice trả phí.",
                next_action="Chỉ dùng account/voice availability check khi có adapter an toàn ở milestone sau.",
                reason_codes=("ELEVENLABS_SMOKE_SKIPPED_NO_TTS",),
                technical_appendix={"adapter_exists": False, "real_tts_generation_added": False},
            ),
        ]

    def safe_config(self) -> dict[str, Any]:
        return {
            "api_key_configured": self.env.secret_configured(self.settings.elevenlabs_api_key),
            "plan_baseline": self.settings.elevenlabs_plan or "Creator",
            "budget_basis": self.settings.elevenlabs_budget_basis or "credits/characters",
            "role": "voice-only",
        }


class CreatomateReadinessCheck(_BaseReadinessCheck):
    provider_key = "creatomate"
    provider_name = "Creatomate"
    provider_type = "CLOUD_TEMPLATE_RENDERER_LIGHT"

    def evaluate(self) -> list[ProviderCheckDraft]:
        key_configured = self.env.secret_configured(self.settings.creatomate_api_key)
        budget_missing = _missing(
            ("CREATOMATE_PLAN", bool(self.settings.creatomate_plan)),
            ("CREATOMATE_MONTHLY_CREDITS", self.settings.creatomate_monthly_credits is not None),
            ("CREATOMATE_MONTHLY_BUDGET_USD", self.settings.creatomate_monthly_budget_usd is not None),
        )
        return [
            self._check(
                "CREDENTIAL",
                "PASS" if key_configured else "BLOCKED",
                "Creatomate API key đã cấu hình." if key_configured else "Thiếu Creatomate API key.",
                next_action=None if key_configured else "Thêm CREATOMATE_API_KEY vào secret manager/.env.",
                reason_codes=("CREATOMATE_API_KEY_CONFIGURED",) if key_configured else ("CREATOMATE_API_KEY_MISSING",),
                technical_appendix={"CREATOMATE_API_KEY": _redacted_presence(key_configured), "missing_env_keys": [] if key_configured else ["CREATOMATE_API_KEY"]},
            ),
            self._check(
                "CAPABILITY",
                "PASS",
                "Creatomate giữ vai trò shorts/cards/thumbnails; không phải renderer ráp video dài trong M12.",
                reason_codes=("CREATOMATE_LIGHT_RENDERER_ONLY",),
                technical_appendix={
                    "role": "CLOUD_TEMPLATE_RENDERER_LIGHT",
                    "not_final_long_form_renderer": True,
                    "final_renderer_execution_added": False,
                },
            ),
            self._check(
                "BUDGET",
                "PASS" if not budget_missing else "WARNING",
                "Budget Creatomate hard-env đã có." if not budget_missing else "Budget Creatomate hard-env chưa đủ.",
                next_action=None if not budget_missing else "Thêm plan/credits/monthly budget Creatomate vào .env.",
                reason_codes=("CREATOMATE_BUDGET_CONFIGURED",) if not budget_missing else ("CREATOMATE_BUDGET_MISSING",),
                technical_appendix={"missing_env_keys": budget_missing, "no_actual_spend_calculation": True},
            ),
            self._check(
                "REAL_SMOKE",
                "SKIPPED",
                "Creatomate smoke mặc định bỏ qua; M12 không render thật.",
                next_action="Chỉ dùng account/template check khi có adapter an toàn, không render long-form.",
                reason_codes=("CREATOMATE_SMOKE_SKIPPED_NO_RENDER",),
                technical_appendix={"adapter_exists": False, "real_render_added": False},
            ),
        ]

    def safe_config(self) -> dict[str, Any]:
        api_key_configured = self.env.secret_configured(self.settings.creatomate_api_key)
        return {
            "api_key_configured": api_key_configured,
            "plan": self.settings.creatomate_plan,
            "role": "Shorts/cards/thumbnails",
            "not_final_long_form_renderer": True,
            "real_render_added": False,
        }


class CloudFinalRendererReadinessCheck(_BaseReadinessCheck):
    provider_key = "cloud-final-renderer"
    provider_name = "Cloud Final Renderer"
    provider_type = "CLOUD_FINAL_ASSEMBLY_RENDERER"

    def evaluate(self) -> list[ProviderCheckDraft]:
        provider_env_present = self.env.string_configured(self.settings.cloud_final_renderer_provider)
        key_configured = self.env.secret_configured(self.settings.cloud_final_renderer_api_key)
        return [
            self._check(
                "CONFIG",
                "BLOCKED",
                "Thiếu renderer ráp video dài; M12 giữ Cloud Final Renderer là required gap.",
                next_action="Chọn renderer ráp video dài sau.",
                reason_codes=("CLOUD_FINAL_RENDERER_REQUIRED_GAP",),
                technical_appendix={
                    "provider_status": "REQUIRED_GAP",
                    "provider_env_present": provider_env_present,
                    "provider_selection_out_of_scope": True,
                    "missing_env_keys": [] if provider_env_present else ["CLOUD_FINAL_RENDERER_PROVIDER"],
                },
            ),
            self._check(
                "CREDENTIAL",
                "SKIPPED" if key_configured else "BLOCKED",
                "Cloud Final Renderer API key có trong env nhưng chưa được dùng vì provider chưa được chọn trong M12."
                if key_configured
                else "Cloud Final Renderer chưa có credential riêng.",
                next_action="Không thêm key final renderer cho đến khi milestone chọn provider." if not key_configured else "Giữ secret ngoài DB; chọn provider ở milestone sau.",
                reason_codes=("CLOUD_FINAL_RENDERER_CREDENTIAL_UNUSED_IN_M12",) if key_configured else ("CLOUD_FINAL_RENDERER_API_KEY_MISSING",),
                technical_appendix={
                    "provider_status": "REQUIRED_GAP",
                    "CLOUD_FINAL_RENDERER_API_KEY": _redacted_presence(key_configured),
                    "missing_env_keys": [] if key_configured else ["CLOUD_FINAL_RENDERER_API_KEY"],
                },
            ),
            self._check(
                "CAPABILITY",
                "BLOCKED",
                "Long-form final render vẫn bị chặn cho đến khi chọn và cấu hình renderer ráp video dài.",
                next_action="Chọn renderer ráp video dài sau; không dùng Creatomate light renderer làm final renderer trong M12.",
                reason_codes=("CLOUD_FINAL_RENDERER_REQUIRED_GAP", "LONG_FORM_FINAL_RENDER_BLOCKED"),
                technical_appendix={
                    "provider_status": "REQUIRED_GAP",
                    "real_final_render_added": False,
                },
            ),
        ]

    def safe_config(self) -> dict[str, Any]:
        api_key_configured = self.env.secret_configured(self.settings.cloud_final_renderer_api_key)
        return {
            "status": "REQUIRED_GAP",
            "configuration_state": "REQUIRED_GAP",
            "provider": "not_selected",
            "provider_env_present": self.env.string_configured(self.settings.cloud_final_renderer_provider),
            "api_key_configured": api_key_configured,
            "ready_for_smoke": False,
            "long_form_final_render_blocked": True,
            "real_final_render_added": False,
            "next_action": "Chọn renderer ráp video dài sau.",
        }


class ProviderReadinessService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.redactor = SecurityRedactionService()

    def readiness(self) -> IntegrationReadinessRead:
        checks, summaries = self._evaluate(persist=False)
        latest = self.session.scalars(select(ProviderReadinessSnapshot).order_by(desc(ProviderReadinessSnapshot.created_at)).limit(1)).one_or_none()
        state, blocking, warning, actions = _snapshot_parts(summaries, checks)
        budget_cards = EnvConfigAuditService(self.settings, self.redactor).budget_cards(summaries)
        return IntegrationReadinessRead(
            generated_at=utc_now(),
            snapshot_state=state,  # type: ignore[arg-type]
            latest_snapshot_id=latest.id if latest else None,
            provider_summaries=summaries,
            checks=[_draft_to_read(check) for check in checks],
            blocking_items=blocking,
            warning_items=warning,
            next_actions=actions,
            budget_cards=budget_cards,
            security_summary={
                "raw_secret_values_exposed": False,
                "env_flags_store_boolean_only": True,
                "local_storage_secrets_allowed": False,
                "plain_db_secret_fields_added": False,
            },
            technical_appendix={"source": "M12 ProviderReadinessService", "no_provider_calls_on_get": True},
        )

    def run(self) -> ProviderReadinessSnapshotRead:
        checks, summaries = self._evaluate(persist=True)
        state, blocking, warning, actions = _snapshot_parts(summaries, checks)
        snapshot = ProviderReadinessSnapshot(
            snapshot_state=state,
            provider_summaries=[summary.model_dump(mode="json") for summary in summaries],
            blocking_items=blocking,
            warning_items=warning,
            next_actions=actions,
        )
        self.session.add(snapshot)
        self.session.flush()
        return ProviderReadinessSnapshotRead.model_validate(snapshot)

    def get_snapshot(self, snapshot_id: uuid.UUID) -> ProviderReadinessSnapshotRead:
        snapshot = self.session.get(ProviderReadinessSnapshot, snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"provider readiness snapshot not found: {snapshot_id}")
        return ProviderReadinessSnapshotRead.model_validate(snapshot)

    def provider_readiness(self, provider_key: str) -> IntegrationReadinessRead:
        normalized = _provider_key(provider_key)
        if normalized not in PROVIDER_ORDER:
            raise NotFoundError(f"provider readiness not found: {provider_key}")
        all_checks, all_summaries = self._evaluate(persist=False)
        checks = [check for check in all_checks if check.provider_key == normalized]
        summaries = [summary for summary in all_summaries if summary.provider_key == normalized]
        state, blocking, warning, actions = _snapshot_parts(summaries, checks)
        return IntegrationReadinessRead(
            generated_at=utc_now(),
            snapshot_state=state,  # type: ignore[arg-type]
            provider_summaries=summaries,
            checks=[_draft_to_read(check) for check in checks],
            blocking_items=blocking,
            warning_items=warning,
            next_actions=actions,
            budget_cards=[],
            security_summary={"raw_secret_values_exposed": False},
            technical_appendix={"source": "M12 provider scoped readiness", "no_provider_calls_on_get": True},
        )

    def _evaluate(self, *, persist: bool) -> tuple[list[ProviderCheckDraft], list[ProviderSummaryRead]]:
        checks: list[ProviderCheckDraft] = []
        summaries: list[ProviderSummaryRead] = []
        for helper in self._helpers():
            provider_checks = helper.evaluate()
            checks.extend(provider_checks)
            summaries.append(helper.summary(provider_checks))
        if persist:
            for check in checks:
                self.session.add(
                    ProviderReadinessCheck(
                        provider_key=check.provider_key,
                        provider_type=check.provider_type,
                        check_type=check.check_type,
                        check_state=check.check_state,
                        operator_summary=check.operator_summary,
                        next_action=check.next_action,
                        reason_codes=list(check.reason_codes),
                        technical_appendix=self.redactor.redact_dict(check.technical_appendix or {}),
                    )
                )
            self.session.flush()
        return checks, summaries

    def _helpers(self) -> list[_BaseReadinessCheck]:
        return [
            OllamaReadinessCheck(self.session, self.settings, self.redactor),
            YouTubePublicReadinessCheck(self.session, self.settings, self.redactor),
            YouTubeOwnerAnalyticsReadinessCheck(self.session, self.settings, self.redactor),
            GoogleDriveReadinessCheck(self.session, self.settings, self.redactor),
            GoogleVertexVeoReadinessCheck(self.session, self.settings, self.redactor),
            ElevenLabsReadinessCheck(self.session, self.settings, self.redactor),
            CreatomateReadinessCheck(self.session, self.settings, self.redactor),
            CloudFinalRendererReadinessCheck(self.session, self.settings, self.redactor),
        ]


class CredentialReadinessService(ProviderReadinessService):
    pass


class IntegrationDashboardReadService(ProviderReadinessService):
    pass


class RealSmokeOrchestratorService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.redactor = SecurityRedactionService()
        self.env = EnvConfigAuditService(self.settings, self.redactor)

    def run_provider(self, provider_key: str, data: ProviderSmokeRequest | None = None) -> RealSmokeRunRead:
        normalized = _provider_key(provider_key)
        if normalized not in PROVIDER_ORDER:
            raise NotFoundError(f"provider smoke not found: {provider_key}")
        smoke_type = data.smoke_type if data and data.smoke_type else "readiness_smoke"
        run = RealSmokeRun(
            provider_key=normalized,
            smoke_type=smoke_type,
            run_state="RUNNING",
            env_flags={},
            started_at=utc_now(),
            technical_appendix={"raw_secret_values_exposed": False},
        )
        self.session.add(run)
        self.session.flush()
        try:
            result = self._dispatch(normalized)
            run.run_state = result["state"]
            run.env_flags = self.redactor.redact_dict(result.get("env_flags", {}))
            run.error_code = result.get("error_code")
            run.error_message = self.redactor.safe_error(result.get("error_message"))
            run.result_summary = result.get("summary")
            run.technical_appendix = self.redactor.redact_dict(result.get("technical_appendix", {}))
        except Exception as exc:
            run.run_state = "FAILED"
            run.error_code = "M12_SMOKE_ORCHESTRATOR_FAILED"
            run.error_message = self.redactor.safe_error(str(exc))
            run.result_summary = "Smoke thất bại trong orchestration guard."
        run.completed_at = utc_now()
        self.session.flush()
        return RealSmokeRunRead.model_validate(run)

    def get_run(self, run_id: uuid.UUID) -> RealSmokeRunRead:
        run = self.session.get(RealSmokeRun, run_id)
        if run is None:
            raise NotFoundError(f"real smoke run not found: {run_id}")
        return RealSmokeRunRead.model_validate(run)

    def _dispatch(self, provider_key: str) -> dict[str, Any]:
        return {
            "ollama": self._ollama,
            "youtube-public": self._youtube_public,
            "youtube-owner": self._youtube_owner,
            "google-drive": self._google_drive,
            "google-vertex-veo": self._google_vertex_veo,
            "elevenlabs": self._elevenlabs,
            "creatomate": self._creatomate,
            "cloud-final-renderer": self._cloud_final_renderer,
        }[provider_key]()

    def _ollama(self) -> dict[str, Any]:
        enabled = self.settings.llm_real_execution_enabled and self.settings.llm_router_real_smoke
        flags = {
            "VCOS_LLM_REAL_EXECUTION_ENABLED": self.settings.llm_real_execution_enabled,
            "VCOS_LLM_ROUTER_REAL_SMOKE": self.settings.llm_router_real_smoke,
            "VCOS_LLM_PROVIDER_IS_OLLAMA": self.settings.llm_provider.lower() == "ollama",
        }
        if not enabled:
            return _smoke_result("SKIPPED", "Ollama smoke bị bỏ qua vì env guard đang tắt.", env_flags=flags, reason="OLLAMA_REAL_SMOKE_SKIPPED")
        lanes = LLMRouterConfigLoader(self.session).list_lanes(profile_key="default")
        configured_models = _ollama_models_from_lanes(lanes)
        health = OllamaLLMProvider(base_url=self.settings.ollama_base_url).list_models()
        if not health.ok:
            return _smoke_result("BLOCKED", "Không kết nối được Ollama để list model.", env_flags=flags, error_code=health.error_code, error_message=health.error_message)
        available = {str(item.get("name") or item.get("model")) for item in health.output.get("models", []) if isinstance(item, dict)}
        missing = sorted(model for model in configured_models if model not in available)
        if missing:
            return _smoke_result(
                "BLOCKED",
                "Một số model Ollama được cấu hình chưa callable.",
                env_flags=flags,
                error_code="OLLAMA_CONFIGURED_MODEL_MISSING",
                technical_appendix={"missing_model_count": len(missing), "available_model_count": len(available)},
            )
        prompts = {
            "cheap_structured": ('Return JSON exactly like {"ok": true, "marker": "cheap_structured"}.', "json"),
            "long_context_text": ("Reply with marker long_context_text in one short sentence.", "text"),
            "visual_creative_review": ("Reply with marker visual_creative_review only.", "text"),
            "gatekeeper_soft_review": ("Reply with marker gatekeeper_soft_review only.", "text"),
        }
        route_results = []
        for lane, (prompt, response_format) in prompts.items():
            route_results.append(
                LLMRouterService(self.session).route(
                    lane_name=lane,
                    prompt=prompt,
                    requested_task_type="m12_readiness_smoke",
                    response_format=response_format,
                    correlation_id=f"m12-ollama-{lane}",
                )
            )
        passed = all(result.status == "SUCCESS" for result in route_results)
        return _smoke_result(
            "PASS" if passed else "FAILED",
            "Ollama smoke 4 lane thành công." if passed else "Ollama smoke có lane thất bại.",
            env_flags=flags,
            technical_appendix={"lanes": [result.lane_name for result in route_results], "statuses": [result.status for result in route_results]},
        )

    def _youtube_public(self) -> dict[str, Any]:
        flags = {
            "VCOS_YOUTUBE_REAL_PUBLIC_SMOKE": self.settings.youtube_real_public_smoke,
            "YOUTUBE_PUBLIC_MONITOR_ENABLED": self.settings.youtube_public_monitor_enabled,
            "YOUTUBE_DATA_API_KEY_CONFIGURED": self.env.secret_configured(self.settings.youtube_data_api_key),
            "YOUTUBE_TEST_VIDEO_ID_CONFIGURED": bool(self.settings.youtube_test_video_id),
        }
        if not self.settings.youtube_real_public_smoke:
            return _smoke_result("SKIPPED", "YouTube public smoke bị bỏ qua vì flag thật đang tắt.", env_flags=flags, reason="YOUTUBE_PUBLIC_SMOKE_SKIPPED")
        if not self.settings.youtube_public_monitor_enabled or not self.settings.youtube_data_api_key or not self.settings.youtube_test_video_id:
            return _smoke_result("BLOCKED", "Thiếu config/API key/test video cho YouTube public smoke.", env_flags=flags, error_code="YOUTUBE_PUBLIC_SMOKE_CONFIG_MISSING")
        result = YouTubePublicStatsProvider().fetch(
            platform_video_id=self.settings.youtube_test_video_id,
            api_key=self.settings.youtube_data_api_key.get_secret_value(),
        )
        if not result.ok:
            return _smoke_result("FAILED", "YouTube Data API smoke thất bại.", env_flags=flags, error_code=result.error_code, error_message=result.error_message)
        output = result.output or {}
        return _smoke_result(
            "PASS",
            "YouTube public stats smoke thành công; views/likes/comments được map không fake.",
            env_flags=flags,
            technical_appendix={
                "views_available": output.get("views") is not None,
                "likes_available": output.get("likes") is not None,
                "comments_available": output.get("comments") is not None,
                "metric_availability": output.get("metric_availability"),
            },
        )

    def _youtube_owner(self) -> dict[str, Any]:
        flags = {
            "VCOS_YOUTUBE_REAL_OWNER_SMOKE": self.settings.youtube_real_owner_smoke,
            "YOUTUBE_OWNER_ANALYTICS_ENABLED": self.settings.youtube_owner_analytics_enabled,
            "YOUTUBE_TEST_VIDEO_ID_CONFIGURED": bool(self.settings.youtube_test_video_id),
        }
        if not self.settings.youtube_real_owner_smoke:
            return _smoke_result("SKIPPED", "YouTube owner smoke bị bỏ qua vì flag thật đang tắt.", env_flags=flags, reason="YOUTUBE_OWNER_SMOKE_SKIPPED")
        credential_service = YouTubeOAuthCredentialService(self.session, config_service=YouTubeMonitoringConfigService(self.settings))
        reference = credential_service.get_connected_reference()
        if reference is None:
            return _smoke_result("BLOCKED", "YouTube owner smoke cần OAuth token.", env_flags={**flags, "token_connected": False}, error_code="NEEDS_AUTH")
        access_token = credential_service.get_valid_access_token(reference)
        if not access_token or not self.settings.youtube_test_video_id:
            return _smoke_result("BLOCKED", "YouTube owner smoke thiếu token hợp lệ hoặc test video.", env_flags={**flags, "token_connected": bool(access_token)}, error_code="YOUTUBE_OWNER_SMOKE_CONFIG_MISSING")
        end_date = date.today()
        start_date = end_date - timedelta(days=7)
        result = YouTubeOwnerAnalyticsProvider().fetch(
            platform_video_id=self.settings.youtube_test_video_id,
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
        )
        if not result.ok:
            return _smoke_result("FAILED", "YouTube owner analytics smoke thất bại; không tạo fake metric.", env_flags={**flags, "token_connected": True}, error_code=result.error_code, error_message=result.error_message)
        return _smoke_result("PASS", "YouTube owner analytics smoke thành công.", env_flags={**flags, "token_connected": True}, technical_appendix={"no_fake_metrics": True})

    def _google_drive(self) -> dict[str, Any]:
        flags = {
            "VCOS_DRIVE_REAL_UPLOAD_SMOKE": self.settings.drive_real_upload_smoke,
            "GOOGLE_DRIVE_OFFLOAD_ENABLED": self.settings.google_drive_offload_enabled,
            "GOOGLE_DRIVE_ROOT_FOLDER_ID_CONFIGURED": bool(self.settings.google_drive_root_folder_id),
        }
        if not self.settings.drive_real_upload_smoke:
            return _smoke_result("SKIPPED", "Google Drive upload smoke bị bỏ qua vì flag thật đang tắt.", env_flags=flags, reason="GOOGLE_DRIVE_SMOKE_SKIPPED")
        credential_service = GoogleDriveOAuthCredentialService(self.session, config_service=GoogleDriveConfigService(self.settings))
        reference = credential_service.get_connected_reference()
        if not self.settings.google_drive_offload_enabled or not self.settings.google_drive_root_folder_id or reference is None:
            return _smoke_result("BLOCKED", "Drive smoke thiếu offload/root folder/OAuth token.", env_flags={**flags, "token_connected": reference is not None}, error_code="GOOGLE_DRIVE_SMOKE_CONFIG_MISSING")
        smoke_dir = ROOT / "var" / "tmp" / "readiness-smoke"
        smoke_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", prefix="m12-drive-", dir=smoke_dir, delete=False) as handle:
            handle.write("vcos m12 google drive readiness smoke\n")
            local_path = Path(handle.name)
        try:
            cloud_ref, verification = GoogleDriveUploadService(self.session).upload_verified(
                local_path=local_path,
                media_type="READINESS_SMOKE",
                company_id=None,
                channel_workspace_id=None,
                video_project_id=None,
                uploaded_video_id=None,
                render_package_id=None,
                source_refs=[{"source": "M12_REAL_SMOKE"}],
                retention_policy={"test_smoke": True, "keep_local": False},
            )
        finally:
            local_path.unlink(missing_ok=True)
        return _smoke_result(
            "PASS",
            "Google Drive tiny upload smoke thành công; local test file đã dọn.",
            env_flags={**flags, "token_connected": True},
            technical_appendix={
                "drive_file_id_present": bool(cloud_ref.drive_file_id),
                "web_view_link_present": bool(cloud_ref.web_view_link),
                "verification_status": verification.verification_status,
                "drive_cleanup_not_attempted": True,
            },
        )

    def _google_vertex_veo(self) -> dict[str, Any]:
        try:
            config = GoogleVertexVeoConfigService(self.session).resolve()
        except Exception as exc:
            return _smoke_result("BLOCKED", "Veo config invalid nên không chạy smoke.", env_flags={}, error_code="VEO_CONFIG_INVALID", error_message=str(exc))
        flags = {
            "VCOS_VEO_REAL_EXECUTION_ENABLED": config.real_execution_enabled,
            "VCOS_VEO_REAL_SMOKE": config.real_smoke_enabled,
            "GOOGLE_CLOUD_PROJECT_ID_CONFIGURED": bool(config.project_id),
            "GOOGLE_CLOUD_LOCATION_CONFIGURED": bool(config.location),
            "GOOGLE_APPLICATION_CREDENTIALS_CONFIGURED": bool(config.service_account_path),
        }
        if not config.real_execution_enabled or not config.real_smoke_enabled:
            return _smoke_result("SKIPPED", "Veo smoke bị bỏ qua vì env guard đang tắt.", env_flags=flags, reason="VEO_SMOKE_SKIPPED")
        if not config.project_id or not config.location or not config.service_account_path:
            return _smoke_result("BLOCKED", "Veo smoke thiếu Google project/location/service account.", env_flags=flags, error_code="VEO_REAL_EXECUTION_CONFIG_MISSING")
        response = GoogleVertexVeoProvider().generate_video(
            request=GoogleVertexVeoRequest(
                prompt="VCOS readiness smoke: abstract operator dashboard pulse, no brand, no people.",
                model=config.model_id or VEO_GA_MODEL_ID,
                mode=config.mode or "video_only",
                resolution=config.resolution or "1080p",
                duration_seconds=Decimal("4"),
                audio_enabled=False,
                output_gcs_uri=None,
            ),
            config=GoogleVertexVeoExecutionConfig(
                project_id=config.project_id,
                location=config.location,
                service_account_path=config.service_account_path,
                real_execution_enabled=config.real_execution_enabled,
                real_smoke_enabled=config.real_smoke_enabled,
            ),
        )
        if not response.ok:
            return _smoke_result("FAILED", "Veo guarded smoke thất bại.", env_flags=flags, error_code=response.error_code, error_message=response.error_message)
        return _smoke_result("PASS", "Veo guarded smoke đã gửi request tối thiểu.", env_flags=flags, technical_appendix={"operation_ref_present": bool(response.output.get("operation_ref"))})

    def _elevenlabs(self) -> dict[str, Any]:
        flags = {
            "VCOS_ELEVENLABS_REAL_ACCOUNT_SMOKE": self.settings.elevenlabs_real_account_smoke,
            "ELEVENLABS_API_KEY_CONFIGURED": self.env.secret_configured(self.settings.elevenlabs_api_key),
        }
        if not self.settings.elevenlabs_real_account_smoke:
            return _smoke_result("SKIPPED", "ElevenLabs smoke bị bỏ qua; không sinh voice mặc định.", env_flags=flags, reason="ELEVENLABS_SMOKE_SKIPPED")
        if not self.env.secret_configured(self.settings.elevenlabs_api_key):
            return _smoke_result("BLOCKED", "Thiếu ElevenLabs API key.", env_flags=flags, error_code="ELEVENLABS_API_KEY_MISSING")
        return _smoke_result("SKIPPED", "Chưa có adapter account-check an toàn; M12 không thêm real TTS.", env_flags=flags, reason="ELEVENLABS_ADAPTER_NOT_AVAILABLE")

    def _creatomate(self) -> dict[str, Any]:
        flags = {
            "VCOS_CREATOMATE_REAL_ACCOUNT_SMOKE": self.settings.creatomate_real_account_smoke,
            "CREATOMATE_API_KEY_CONFIGURED": self.env.secret_configured(self.settings.creatomate_api_key),
        }
        if not self.settings.creatomate_real_account_smoke:
            return _smoke_result("SKIPPED", "Creatomate smoke bị bỏ qua; không render mặc định.", env_flags=flags, reason="CREATOMATE_SMOKE_SKIPPED")
        if not self.env.secret_configured(self.settings.creatomate_api_key):
            return _smoke_result("BLOCKED", "Thiếu Creatomate API key.", env_flags=flags, error_code="CREATOMATE_API_KEY_MISSING")
        return _smoke_result("SKIPPED", "Chưa có adapter account/template-check an toàn; M12 không render thật.", env_flags=flags, reason="CREATOMATE_ADAPTER_NOT_AVAILABLE")

    def _cloud_final_renderer(self) -> dict[str, Any]:
        key_configured = self.env.secret_configured(self.settings.cloud_final_renderer_api_key)
        flags = {
            "CLOUD_FINAL_RENDERER_PROVIDER_SELECTED": False,
            "CLOUD_FINAL_RENDERER_API_KEY_CONFIGURED": key_configured,
            "LONG_FORM_FINAL_RENDER_BLOCKED": True,
        }
        return _smoke_result(
            "BLOCKED",
            "Cloud Final Renderer là required gap trong M12; không có smoke/render thật.",
            env_flags=flags,
            error_code="CLOUD_FINAL_RENDERER_REQUIRED_GAP",
            technical_appendix={"provider_status": "REQUIRED_GAP", "real_final_render_added": False},
        )


def _smoke_result(
    state: str,
    summary: str,
    *,
    env_flags: dict[str, Any],
    reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    technical_appendix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    appendix = dict(technical_appendix or {})
    if reason:
        appendix["reason_code"] = reason
    return {
        "state": state,
        "summary": summary,
        "env_flags": env_flags,
        "error_code": error_code,
        "error_message": error_message,
        "technical_appendix": appendix,
    }


def _provider_key(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "ollama-router": "ollama",
        "youtube-public-monitor": "youtube-public",
        "youtube-owner-analytics": "youtube-owner",
        "drive": "google-drive",
        "google-drive-offload": "google-drive",
        "veo": "google-vertex-veo",
        "google-vertex": "google-vertex-veo",
        "cloud-final": "cloud-final-renderer",
    }
    return aliases.get(normalized, normalized)


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower().replace("-", "_")


def _redacted_presence(value: Any) -> Any:
    return {"configured": bool(value), "redacted": True}


def _missing(*items: tuple[str, bool]) -> list[str]:
    return [key for key, configured in items if not configured]


def _ollama_models_from_lanes(lanes: list[Any]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for lane in lanes:
        candidates = [lane.primary_model, *lane.fallback_models, lane.premium_model, lane.emergency_model, lane.backup_model]
        for model in candidates:
            if model and model not in seen:
                seen.add(model)
                models.append(model)
    return models


def _provider_state(checks: list[ProviderCheckDraft]) -> str:
    states = [check.check_state for check in checks]
    if any(state in {"BLOCKED", "FAILED"} for state in states):
        return "BLOCKED"
    if any(state in {"WARNING", "UNKNOWN"} for state in states):
        return "WARNING"
    if any(state == "SKIPPED" for state in states):
        return "WARNING"
    return "PASS"


def _smoke_state(checks: list[ProviderCheckDraft]) -> str | None:
    for check in checks:
        if check.check_type == "REAL_SMOKE":
            return check.check_state
    return None


def _status_label(state: str) -> str:
    return {
        "PASS": "Đã sẵn sàng",
        "WARNING": "Cần cấu hình",
        "BLOCKED": "Bị chặn",
        "FAILED": "Smoke thất bại",
        "SKIPPED": "Smoke bị bỏ qua",
        "UNKNOWN": "Chưa có dữ liệu",
    }.get(state, "Chưa có dữ liệu")


def _summary_for_state(provider_name: str, state: str) -> str:
    if state == "PASS":
        return f"{provider_name} đã sẵn sàng theo readiness check."
    if state == "BLOCKED":
        return f"{provider_name} đang bị chặn hoặc thiếu credential/config."
    return f"{provider_name} cần kiểm tra thêm trước production."


def _missing_from_checks(checks: list[ProviderCheckDraft]) -> list[str]:
    missing: list[str] = []
    for check in checks:
        appendix = check.technical_appendix or {}
        values = appendix.get("missing_env_keys")
        if isinstance(values, list):
            missing.extend(str(item) for item in values)
    return missing


def _draft_to_read(check: ProviderCheckDraft) -> ProviderReadinessCheckRead:
    return ProviderReadinessCheckRead(
        provider_key=check.provider_key,
        provider_type=check.provider_type,
        check_type=check.check_type,  # type: ignore[arg-type]
        check_state=check.check_state,  # type: ignore[arg-type]
        operator_summary=check.operator_summary,
        next_action=check.next_action,
        reason_codes=list(check.reason_codes),
        technical_appendix=check.technical_appendix or {},
    )


def _snapshot_parts(
    summaries: list[ProviderSummaryRead],
    checks: list[ProviderCheckDraft],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    blocking = [
        {
            "provider_key": check.provider_key,
            "check_type": check.check_type,
            "summary": check.operator_summary,
            "next_action": check.next_action,
            "reason_codes": list(check.reason_codes),
        }
        for check in checks
        if check.check_state in {"BLOCKED", "FAILED"}
    ]
    warning = [
        {
            "provider_key": check.provider_key,
            "check_type": check.check_type,
            "summary": check.operator_summary,
            "next_action": check.next_action,
            "reason_codes": list(check.reason_codes),
        }
        for check in checks
        if check.check_state in {"WARNING", "UNKNOWN", "SKIPPED"}
    ]
    actions = [
        {"provider_key": summary.provider_key, "next_action": summary.next_action, "readiness_state": summary.readiness_state}
        for summary in summaries
        if summary.next_action
    ]
    if blocking:
        state = "BLOCKED"
    elif warning:
        state = "PARTIAL"
    elif summaries:
        state = "READY"
    else:
        state = "UNKNOWN"
    return state, blocking, warning, actions

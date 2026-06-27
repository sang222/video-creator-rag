from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, ChannelProfileVersionCreate, EventEnvelope
from app.contracts.m11_1 import (
    AuthSessionRead,
    ChannelLocalizationConfig,
    ChannelLocalizationConfigUpdate,
    ChannelPublishTimingPolicyCreate,
    ChannelPublishTimingPolicyRead,
    CurrentOperatorUserRead,
    LocalizationReadinessGateRead,
    LocalizedMetadataPackageCreate,
    LocalizedMetadataPackageRead,
    LocalizedSubtitlePackageCreate,
    LocalizedSubtitlePackageRead,
    PublishTimingSuggestionRead,
    VideoProjectLocalizationRead,
)
from app.contracts.profile import ChannelProfileInput
from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ChannelProfileVersion,
    ChannelPublishTimingPolicy,
    ChannelWorkspace,
    CloudMediaRef,
    LocalizedMetadataPackage,
    LocalizedSubtitlePackage,
    OperatorAuthSession,
    OperatorUser,
    PublishHandoffPackage,
    PublishTimingSuggestion,
    VideoProject,
)
from app.services.audit import AuditService
from app.services.channel_profile import ChannelProfileService
from app.services.domain_events import DomainEventBus
from app.services.profile_compiler import ChannelProfileCompiler


AUTH_COOKIE_NAME = "vcos_operator_session"
PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 210_000
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
DAYS = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)).hex()
        return hmac.compare_digest(expected, digest_hex)
    except Exception:
        return False


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def bootstrap_admin_if_needed(self) -> tuple[OperatorUser | None, bool]:
        existing_count = int(self.session.scalar(select(func.count()).select_from(OperatorUser)) or 0)
        if existing_count > 0:
            return None, False
        if not self.settings.bootstrap_admin_email or self.settings.bootstrap_admin_password is None:
            return None, False
        role = self.settings.bootstrap_admin_role
        if role not in {
            "OWNER_ADMIN",
            "CHANNEL_MANAGER",
            "PRODUCER",
            "REVIEWER",
            "PUBLISHER",
            "ANALYST",
            "PROCUREMENT_OPERATOR",
            "COMPLIANCE_REVIEWER",
            "LEARNING_REVIEWER",
            "READ_ONLY",
        }:
            raise ValidationFailureError("bootstrap admin role is invalid")
        user = OperatorUser(
            email=self.settings.bootstrap_admin_email.lower(),
            password_hash=hash_password(self.settings.bootstrap_admin_password.get_secret_value()),
            display_name="VCOS Admin",
            role=role,
            status="ACTIVE",
        )
        self.session.add(user)
        self.session.flush()
        _audit(
            self.session,
            action="auth.bootstrap_admin_created",
            target_type="operator_user",
            target_id=user.id,
            reason_code="BOOTSTRAP_ADMIN_CREATED",
            payload={"email": user.email, "role": user.role, "password_plaintext_stored": False},
        )
        return user, True

    def login(self, *, email: str, password: str) -> tuple[AuthSessionRead, str, datetime]:
        if self.settings.auth_mode != "local_password":
            raise ValidationFailureError("auth_mode hiện chưa hỗ trợ")
        self.bootstrap_admin_if_needed()
        user = self.session.scalars(select(OperatorUser).where(OperatorUser.email == email.lower())).one_or_none()
        if user is None or user.status != "ACTIVE" or not verify_password(password, user.password_hash):
            _audit(
                self.session,
                action="auth.login_failed",
                target_type="operator_user",
                target_id=None,
                reason_code="LOGIN_FAILED",
                payload={"email": email.lower()},
            )
            raise ForbiddenError("Email hoặc mật khẩu không đúng.")
        token = secrets.token_urlsafe(48)
        expires_at = utc_now() + timedelta(hours=self.settings.auth_session_ttl_hours)
        auth_session = OperatorAuthSession(user_id=user.id, session_token_hash=hash_session_token(token), expires_at=expires_at)
        self.session.add(auth_session)
        self.session.flush()
        _audit(
            self.session,
            action="auth.login_success",
            target_type="operator_user",
            target_id=user.id,
            reason_code="SESSION_CREATED",
            payload={"session_id": str(auth_session.id), "http_only_cookie": True},
        )
        return self._auth_read(user), token, expires_at

    def current_user(self, token: str | None) -> AuthSessionRead:
        if not self.settings.dashboard_auth_enabled:
            return AuthSessionRead(
                authenticated=True,
                auth_enabled=False,
                auth_mode=self.settings.auth_mode,
                local_dev_note="Chế độ local/dev - auth dashboard đang tắt.",
                user=None,
            )
        if not token:
            raise ForbiddenError("Phiên đăng nhập đã hết hạn.")
        auth_session = self.session.scalars(
            select(OperatorAuthSession).where(OperatorAuthSession.session_token_hash == hash_session_token(token)).limit(1)
        ).one_or_none()
        if auth_session is None or auth_session.revoked_at is not None or auth_session.expires_at <= utc_now():
            raise ForbiddenError("Phiên đăng nhập đã hết hạn.")
        user = self.session.get(OperatorUser, auth_session.user_id)
        if user is None or user.status != "ACTIVE":
            raise ForbiddenError("Bạn chưa có quyền thực hiện thao tác này.")
        return self._auth_read(user)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        auth_session = self.session.scalars(
            select(OperatorAuthSession).where(OperatorAuthSession.session_token_hash == hash_session_token(token)).limit(1)
        ).one_or_none()
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = utc_now()
            _audit(
                self.session,
                action="auth.logout_success",
                target_type="operator_auth_session",
                target_id=auth_session.id,
                reason_code="SESSION_REVOKED",
                payload={"session_token_exposed": False},
            )

    def _auth_read(self, user: OperatorUser) -> AuthSessionRead:
        return AuthSessionRead(
            authenticated=True,
            auth_enabled=self.settings.dashboard_auth_enabled,
            auth_mode=self.settings.auth_mode,
            local_dev_note="Chế độ local/dev - chưa phải đăng nhập production",
            user=CurrentOperatorUserRead(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                role=user.role,
                status=user.status,
            ),
        )


class LocalizationConfigService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, channel_id: uuid.UUID) -> ChannelLocalizationConfig:
        channel = self._channel(channel_id)
        return _localization_config_read(channel, technical_appendix={"source": "channel_workspaces"})

    def update(self, channel_id: uuid.UUID, data: ChannelLocalizationConfigUpdate) -> ChannelLocalizationConfig:
        if data.actor_role not in {"OWNER_ADMIN", "CHANNEL_MANAGER"}:
            raise ForbiddenError("Bạn chưa có quyền thực hiện thao tác này.")
        channel = self._channel(channel_id)
        _validate_language(data.primary_language)
        for value in [*data.target_subtitle_languages, *data.target_metadata_languages]:
            _validate_language(value)
        _validate_timezone(data.primary_timezone)
        channel.primary_language = data.primary_language
        channel.primary_region = data.primary_region
        channel.primary_timezone = data.primary_timezone
        channel.default_timezone = data.primary_timezone
        channel.target_subtitle_languages = sorted(set(data.target_subtitle_languages))
        channel.target_metadata_languages = sorted(set(data.target_metadata_languages))
        channel.target_regions = sorted(set(data.target_regions))
        channel.translation_mode = data.translation_mode
        channel.localization_required_for_publish = data.localization_required_for_publish
        channel.localized_metadata_required = data.localized_metadata_required
        technical: dict[str, Any] = {"profile_snapshot_created": False}
        latest_profile = self.session.scalars(
            select(ChannelProfileVersion)
            .where(ChannelProfileVersion.channel_workspace_id == channel.id)
            .order_by(ChannelProfileVersion.version.desc())
            .limit(1)
        ).one_or_none()
        if latest_profile is not None:
            payload = dict(latest_profile.profile_input)
            policies = dict(payload.get("policies") or {})
            policies["localization"] = _config_payload(channel)
            payload["policies"] = policies
            profile_input = ChannelProfileInput.model_validate(payload)
            profile = ChannelProfileService(self.session).create_profile_version(
                channel_id=channel.id,
                data=ChannelProfileVersionCreate(profile_input=profile_input, created_by=data.edited_by_user_id),
                correlation_id="m11-1-localization-config",
            )
            compiled = ChannelProfileCompiler(self.session).compile(profile_version_id=profile.id, correlation_id="m11-1-localization-config")
            ChannelProfileService(self.session).activate_snapshot(snapshot_id=compiled.snapshot_id, correlation_id="m11-1-localization-config")
            technical = {
                "profile_snapshot_created": True,
                "channel_profile_version_id": str(profile.id),
                "compiled_policy_snapshot_id": str(compiled.snapshot_id),
                "existing_projects_keep_policy_snapshot_id": True,
            }
        _audit(
            self.session,
            action="localization.config_updated",
            target_type="channel_workspace",
            target_id=channel.id,
            reason_code="LOCALIZATION_CONFIG_UPDATED",
            payload={"human_config_only": True, "no_auto_country_targeting": True, "translation_mode": data.translation_mode},
            company_id=channel.company_id,
        )
        _event(
            self.session,
            event_type="localization.config_updated",
            aggregate_type="channel_workspace",
            aggregate_id=channel.id,
            company_id=channel.company_id,
            payload={"target_subtitle_languages": channel.target_subtitle_languages, "target_metadata_languages": channel.target_metadata_languages},
        )
        return _localization_config_read(channel, technical_appendix=technical)

    def _channel(self, channel_id: uuid.UUID) -> ChannelWorkspace:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        return channel


class LocalizedSubtitlePackageService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, video_project_id: uuid.UUID, data: LocalizedSubtitlePackageCreate) -> LocalizedSubtitlePackageRead:
        project = _project(self.session, video_project_id)
        _validate_language(data.source_language)
        _validate_language(data.target_language)
        for ref_id in [data.srt_cloud_media_ref_id, data.vtt_cloud_media_ref_id]:
            if ref_id is not None:
                _drive_ref(self.session, ref_id, project.video_project_id if hasattr(project, "video_project_id") else project.id)
        package = LocalizedSubtitlePackage(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            base_caption_track_id=data.base_caption_track_id,
            source_language=data.source_language,
            target_language=data.target_language,
            srt_cloud_media_ref_id=data.srt_cloud_media_ref_id,
            vtt_cloud_media_ref_id=data.vtt_cloud_media_ref_id,
            translation_status=data.translation_status,
            human_review_status=data.human_review_status,
            reviewer_id=data.reviewer_id,
            quality_notes=data.quality_notes,
            disclosure_notes=data.disclosure_notes,
        )
        self.session.add(package)
        self.session.flush()
        _audit(self.session, action="localization.subtitle_package_created", target_type="localized_subtitle_package", target_id=package.id, reason_code="LOCALIZED_SUBTITLE_PACKAGE_CREATED", payload={"drive_cta_only": True}, company_id=project.company_id)
        return localized_subtitle_package_read(self.session, package)

    def get(self, package_id: uuid.UUID) -> LocalizedSubtitlePackageRead:
        package = self.session.get(LocalizedSubtitlePackage, package_id)
        if package is None:
            raise NotFoundError(f"localized subtitle package not found: {package_id}")
        return localized_subtitle_package_read(self.session, package)


class LocalizedMetadataPackageService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, video_project_id: uuid.UUID, data: LocalizedMetadataPackageCreate) -> LocalizedMetadataPackageRead:
        project = _project(self.session, video_project_id)
        _validate_language(data.language)
        if data.human_review_status == "APPROVED" and _looks_like_keyword_stuffing(data.localized_tags):
            raise ValidationFailureError("Metadata theo ngôn ngữ có dấu hiệu keyword stuffing.")
        package = LocalizedMetadataPackage(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            language=data.language,
            region=data.region,
            localized_title=data.localized_title,
            localized_description=data.localized_description,
            localized_tags=data.localized_tags,
            localized_disclosure_notes=data.localized_disclosure_notes,
            localized_cta_text=data.localized_cta_text,
            human_review_status=data.human_review_status,
            reviewer_id=data.reviewer_id,
            quality_notes=data.quality_notes,
        )
        self.session.add(package)
        self.session.flush()
        _audit(self.session, action="localization.metadata_package_created", target_type="localized_metadata_package", target_id=package.id, reason_code="LOCALIZED_METADATA_PACKAGE_CREATED", payload={"human_review_required": True}, company_id=project.company_id)
        return localized_metadata_package_read(package)

    def get(self, package_id: uuid.UUID) -> LocalizedMetadataPackageRead:
        package = self.session.get(LocalizedMetadataPackage, package_id)
        if package is None:
            raise NotFoundError(f"localized metadata package not found: {package_id}")
        return localized_metadata_package_read(package)


class LocalizationReadinessGateService:
    def __init__(self, session: Session):
        self.session = session

    def video_localization(self, video_project_id: uuid.UUID) -> VideoProjectLocalizationRead:
        project = _project(self.session, video_project_id)
        subtitles = self._subtitle_packages(project.id)
        metadata = self._metadata_packages(project.id)
        readiness = self.check(video_project_id)
        return VideoProjectLocalizationRead(
            video_project_id=project.id,
            subtitle_packages=[localized_subtitle_package_read(self.session, item) for item in subtitles],
            metadata_packages=[localized_metadata_package_read(item) for item in metadata],
            readiness=readiness.model_dump(mode="json"),
            operator_summary=readiness.operator_summary,
        )

    def check(self, video_project_id: uuid.UUID) -> LocalizationReadinessGateRead:
        project = _project(self.session, video_project_id)
        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {project.channel_workspace_id}")
        if channel.translation_mode == "DISABLED" and not channel.localization_required_for_publish:
            return LocalizationReadinessGateRead(
                video_project_id=project.id,
                result="NOT_REQUIRED",
                disclosure_translation_status="NOT_REQUIRED",
                operator_summary="Localization là tùy chọn cho video này.",
                next_action="Có thể publish bản tiếng Anh trước; phụ đề dịch có thể bổ sung sau.",
                technical_appendix={"translation_mode": channel.translation_mode},
            )
        subtitle_by_lang = {item.target_language: item for item in self._subtitle_packages(project.id)}
        metadata_by_lang = {item.language: item for item in self._metadata_packages(project.id)}
        missing_subtitles = [lang for lang in channel.target_subtitle_languages if lang not in subtitle_by_lang]
        missing_metadata = [lang for lang in channel.target_metadata_languages if lang not in metadata_by_lang]
        unreviewed_subtitles = [
            lang
            for lang, item in subtitle_by_lang.items()
            if lang in channel.target_subtitle_languages and item.human_review_status not in {"APPROVED", "NOT_REQUIRED"}
        ]
        unreviewed_metadata = [
            lang
            for lang, item in metadata_by_lang.items()
            if lang in channel.target_metadata_languages and item.human_review_status != "APPROVED"
        ]
        has_blocker = bool(missing_subtitles or unreviewed_subtitles or (channel.localized_metadata_required and (missing_metadata or unreviewed_metadata)))
        result = "BLOCK" if channel.localization_required_for_publish and has_blocker else "REVIEW_REQUIRED" if has_blocker else "PASS"
        if result == "PASS":
            summary = "Gói localization đã đủ phần người duyệt theo cấu hình kênh."
            next_action = "Có thể tiếp tục gói publish thủ công; không auto publish."
        elif result == "BLOCK":
            summary = _readiness_summary(missing_subtitles, missing_metadata, unreviewed_subtitles, unreviewed_metadata)
            next_action = "Hoàn tất subtitle/metadata còn thiếu trước khi publish vì policy kênh yêu cầu localization."
        else:
            summary = _readiness_summary(missing_subtitles, missing_metadata, unreviewed_subtitles, unreviewed_metadata)
            next_action = "Có thể publish bản tiếng Anh trước; phụ đề dịch có thể bổ sung sau."
        return LocalizationReadinessGateRead(
            video_project_id=project.id,
            result=result,
            missing_subtitle_languages=missing_subtitles,
            missing_metadata_languages=missing_metadata,
            unreviewed_subtitle_languages=unreviewed_subtitles,
            unreviewed_metadata_languages=unreviewed_metadata,
            disclosure_translation_status="REVIEW_REQUIRED" if unreviewed_metadata else "READY",
            operator_summary=summary,
            next_action=next_action,
            technical_appendix={
                "translation_mode": channel.translation_mode,
                "localization_required_for_publish": channel.localization_required_for_publish,
                "localized_metadata_required": channel.localized_metadata_required,
                "no_auto_translate": True,
                "no_auto_publish": True,
            },
        )

    def _subtitle_packages(self, video_project_id: uuid.UUID) -> list[LocalizedSubtitlePackage]:
        return list(
            self.session.scalars(
                select(LocalizedSubtitlePackage).where(LocalizedSubtitlePackage.video_project_id == video_project_id).order_by(LocalizedSubtitlePackage.created_at.desc())
            ).all()
        )

    def _metadata_packages(self, video_project_id: uuid.UUID) -> list[LocalizedMetadataPackage]:
        return list(
            self.session.scalars(
                select(LocalizedMetadataPackage).where(LocalizedMetadataPackage.video_project_id == video_project_id).order_by(LocalizedMetadataPackage.created_at.desc())
            ).all()
        )


class PublishTimingPolicyService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, channel_id: uuid.UUID) -> ChannelPublishTimingPolicyRead:
        policy = self.session.scalars(select(ChannelPublishTimingPolicy).where(ChannelPublishTimingPolicy.channel_workspace_id == channel_id)).one_or_none()
        if policy is None:
            channel = self.session.get(ChannelWorkspace, channel_id)
            if channel is None:
                raise NotFoundError(f"channel not found: {channel_id}")
            policy = ChannelPublishTimingPolicy(channel_workspace_id=channel.id, primary_timezone=channel.primary_timezone or channel.default_timezone or "UTC")
            self.session.add(policy)
            self.session.flush()
        return publish_timing_policy_read(policy)

    def update(self, channel_id: uuid.UUID, data: ChannelPublishTimingPolicyCreate) -> ChannelPublishTimingPolicyRead:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        _validate_timezone(data.primary_timezone)
        if data.operator_timezone:
            _validate_timezone(data.operator_timezone)
        _validate_windows(data.preferred_publish_windows)
        policy = self.session.scalars(select(ChannelPublishTimingPolicy).where(ChannelPublishTimingPolicy.channel_workspace_id == channel_id)).one_or_none()
        if policy is None:
            policy = ChannelPublishTimingPolicy(channel_workspace_id=channel_id, primary_timezone=data.primary_timezone)
            self.session.add(policy)
        policy.primary_timezone = data.primary_timezone
        policy.operator_timezone = data.operator_timezone
        policy.target_regions = data.target_regions
        policy.primary_audience_country = data.primary_audience_country
        policy.preferred_publish_windows = data.preferred_publish_windows
        policy.avoid_publish_windows = data.avoid_publish_windows
        policy.publish_days = data.publish_days
        policy.weekend_allowed = data.weekend_allowed
        policy.notes = data.notes
        channel.primary_timezone = data.primary_timezone
        channel.default_timezone = data.primary_timezone
        self.session.flush()
        _audit(self.session, action="publish_timing.policy_updated", target_type="channel_workspace", target_id=channel.id, reason_code="PUBLISH_TIMING_POLICY_UPDATED", payload={"no_auto_schedule": True}, company_id=channel.company_id)
        return publish_timing_policy_read(policy)


class PublishTimingSuggestionService:
    def __init__(self, session: Session):
        self.session = session

    def create_for_handoff(self, handoff_id: uuid.UUID) -> PublishTimingSuggestionRead:
        handoff = self.session.get(PublishHandoffPackage, handoff_id)
        if handoff is None:
            raise NotFoundError(f"publish handoff not found: {handoff_id}")
        policy = PublishTimingPolicyService(self.session).get(handoff.channel_workspace_id)
        local_dt, utc_dt, operator_dt = _suggest_time(policy)
        suggestion = PublishTimingSuggestion(
            channel_workspace_id=handoff.channel_workspace_id,
            video_project_id=handoff.video_project_id,
            publish_handoff_package_id=handoff.id,
            target_timezone=policy.primary_timezone,
            operator_timezone=policy.operator_timezone,
            suggested_publish_at_local=local_dt,
            suggested_publish_at_utc=utc_dt,
            operator_local_time=operator_dt,
            source="CHANNEL_CONFIG",
            confidence_label="CONFIGURED" if policy.preferred_publish_windows else "UNKNOWN",
            operator_summary="Khung giờ publish đã cấu hình. Human vẫn quyết định giờ publish thực tế; VCOS không auto-schedule.",
        )
        self.session.add(suggestion)
        self.session.flush()
        _audit(self.session, action="publish_timing.suggestion_created", target_type="publish_handoff_package", target_id=handoff.id, reason_code="PUBLISH_TIMING_SUGGESTION_CREATED", payload={"source": "CHANNEL_CONFIG", "no_auto_schedule": True}, company_id=handoff.company_id)
        return publish_timing_suggestion_read(suggestion)

    def get(self, suggestion_id: uuid.UUID) -> PublishTimingSuggestionRead:
        suggestion = self.session.get(PublishTimingSuggestion, suggestion_id)
        if suggestion is None:
            raise NotFoundError(f"publish timing suggestion not found: {suggestion_id}")
        return publish_timing_suggestion_read(suggestion)


def localized_subtitle_package_read(session: Session, package: LocalizedSubtitlePackage) -> LocalizedSubtitlePackageRead:
    ctas = []
    for ref_id, label in [(package.srt_cloud_media_ref_id, "SRT"), (package.vtt_cloud_media_ref_id, "VTT")]:
        if ref_id is None:
            continue
        ref = session.get(CloudMediaRef, ref_id)
        if ref is not None:
            ctas.append({"label": f"Mở file phụ đề {label} trên Google Drive", "web_view_link": ref.web_view_link, "file_name": ref.file_name})
    return LocalizedSubtitlePackageRead(
        id=package.id,
        company_id=package.company_id,
        channel_workspace_id=package.channel_workspace_id,
        video_project_id=package.video_project_id,
        base_caption_track_id=package.base_caption_track_id,
        source_language=package.source_language,
        target_language=package.target_language,
        srt_cloud_media_ref_id=package.srt_cloud_media_ref_id,
        vtt_cloud_media_ref_id=package.vtt_cloud_media_ref_id,
        translation_status=package.translation_status,
        human_review_status=package.human_review_status,
        reviewer_id=package.reviewer_id,
        quality_notes=package.quality_notes,
        disclosure_notes=package.disclosure_notes,
        google_drive_ctas=ctas,
        operator_summary=f"Phụ đề {package.target_language} đang ở trạng thái cần người duyệt." if package.human_review_status != "APPROVED" else f"Phụ đề {package.target_language} đã được duyệt.",
        created_at=package.created_at,
        updated_at=package.updated_at,
        technical_appendix={"no_local_path": True, "drive_cta_only": True},
    )


def localized_metadata_package_read(package: LocalizedMetadataPackage) -> LocalizedMetadataPackageRead:
    return LocalizedMetadataPackageRead(
        id=package.id,
        company_id=package.company_id,
        channel_workspace_id=package.channel_workspace_id,
        video_project_id=package.video_project_id,
        language=package.language,
        region=package.region,
        localized_title=package.localized_title,
        localized_description=package.localized_description,
        localized_tags=package.localized_tags,
        localized_disclosure_notes=package.localized_disclosure_notes,
        localized_cta_text=package.localized_cta_text,
        human_review_status=package.human_review_status,
        reviewer_id=package.reviewer_id,
        quality_notes=package.quality_notes,
        operator_summary=f"Metadata {package.language} cần người duyệt trước khi dùng." if package.human_review_status != "APPROVED" else f"Metadata {package.language} đã được duyệt.",
        created_at=package.created_at,
        updated_at=package.updated_at,
        technical_appendix={"no_keyword_stuffing": True, "no_auto_publish": True},
    )


def publish_timing_policy_read(policy: ChannelPublishTimingPolicy) -> ChannelPublishTimingPolicyRead:
    return ChannelPublishTimingPolicyRead(
        id=policy.id,
        channel_workspace_id=policy.channel_workspace_id,
        primary_timezone=policy.primary_timezone,
        operator_timezone=policy.operator_timezone,
        target_regions=policy.target_regions,
        primary_audience_country=policy.primary_audience_country,
        preferred_publish_windows=policy.preferred_publish_windows,
        avoid_publish_windows=policy.avoid_publish_windows,
        publish_days=policy.publish_days,
        weekend_allowed=policy.weekend_allowed,
        notes=policy.notes,
        operator_summary="Khung giờ publish đã cấu hình theo timezone kênh. Đây không phải khuyến nghị algorithm.",
        created_at=policy.created_at,
        updated_at=policy.updated_at,
        technical_appendix={"no_auto_schedule": True, "iana_timezone_required": True},
    )


def publish_timing_suggestion_read(suggestion: PublishTimingSuggestion) -> PublishTimingSuggestionRead:
    return PublishTimingSuggestionRead(
        id=suggestion.id,
        channel_workspace_id=suggestion.channel_workspace_id,
        video_project_id=suggestion.video_project_id,
        publish_handoff_package_id=suggestion.publish_handoff_package_id,
        target_timezone=suggestion.target_timezone,
        operator_timezone=suggestion.operator_timezone,
        suggested_publish_at_local=suggestion.suggested_publish_at_local,
        suggested_publish_at_utc=suggestion.suggested_publish_at_utc,
        operator_local_time=suggestion.operator_local_time,
        source=suggestion.source,
        confidence_label=suggestion.confidence_label,
        operator_summary=suggestion.operator_summary,
        created_at=suggestion.created_at,
    )


def _localization_config_read(channel: ChannelWorkspace, *, technical_appendix: dict[str, Any]) -> ChannelLocalizationConfig:
    return ChannelLocalizationConfig(
        channel_workspace_id=channel.id,
        primary_language=channel.primary_language,
        primary_region=channel.primary_region,
        primary_timezone=channel.primary_timezone or channel.default_timezone,
        target_subtitle_languages=channel.target_subtitle_languages,
        target_metadata_languages=channel.target_metadata_languages,
        target_regions=channel.target_regions,
        translation_mode=channel.translation_mode,
        localization_required_for_publish=channel.localization_required_for_publish,
        localized_metadata_required=channel.localized_metadata_required,
        operator_summary="Cấu hình localization do human quản lý. VCOS không tự chọn quốc gia hoặc tự publish bản dịch.",
        technical_appendix=technical_appendix,
    )


def _config_payload(channel: ChannelWorkspace) -> dict[str, Any]:
    return {
        "primary_language": channel.primary_language,
        "primary_region": channel.primary_region,
        "primary_timezone": channel.primary_timezone,
        "target_subtitle_languages": channel.target_subtitle_languages,
        "target_metadata_languages": channel.target_metadata_languages,
        "target_regions": channel.target_regions,
        "translation_mode": channel.translation_mode,
        "localization_required_for_publish": channel.localization_required_for_publish,
        "localized_metadata_required": channel.localized_metadata_required,
        "human_config_only": True,
    }


def _project(session: Session, project_id: uuid.UUID) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise NotFoundError(f"video project not found: {project_id}")
    return project


def _drive_ref(session: Session, ref_id: uuid.UUID, project_id: uuid.UUID) -> CloudMediaRef:
    ref = session.get(CloudMediaRef, ref_id)
    if ref is None:
        raise NotFoundError(f"cloud media ref not found: {ref_id}")
    if ref.storage_provider != "GOOGLE_DRIVE":
        raise ValidationFailureError("Subtitle file phải dùng CloudMediaRef trên Google Drive.")
    if ref.video_project_id not in {None, project_id}:
        raise ValidationFailureError("CloudMediaRef không thuộc video project này.")
    return ref


def _validate_language(value: str) -> None:
    if not LANGUAGE_RE.match(value):
        raise ValidationFailureError("Mã ngôn ngữ không hợp lệ.")


def _validate_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValidationFailureError("Timezone phải là IANA timezone hợp lệ.") from exc


def _validate_windows(windows: list[dict[str, Any]]) -> None:
    for window in windows:
        timezone = str(window.get("timezone") or "")
        if timezone:
            _validate_timezone(timezone)
        day = str(window.get("day_of_week") or "").upper()
        if day and day not in DAYS:
            raise ValidationFailureError("day_of_week không hợp lệ.")
        _parse_hhmm(str(window.get("local_time_start") or "09:00"))
        _parse_hhmm(str(window.get("local_time_end") or "11:00"))


def _looks_like_keyword_stuffing(tags: list[str]) -> bool:
    normalized = [tag.strip().lower() for tag in tags if tag.strip()]
    return len(normalized) > 20 or len(normalized) != len(set(normalized))


def _readiness_summary(
    missing_subtitles: list[str],
    missing_metadata: list[str],
    unreviewed_subtitles: list[str],
    unreviewed_metadata: list[str],
) -> str:
    if unreviewed_subtitles:
        return f"Phụ đề {unreviewed_subtitles[0]} đang chờ người duyệt."
    if missing_subtitles:
        return f"Đang thiếu phụ đề {missing_subtitles[0]}."
    if unreviewed_metadata:
        return f"Metadata {unreviewed_metadata[0]} đang chờ người duyệt."
    if missing_metadata:
        return f"Metadata {missing_metadata[0]} đang thiếu phần disclosure."
    return "Chưa đủ dữ liệu để kết luận."


def _suggest_time(policy: ChannelPublishTimingPolicyRead) -> tuple[datetime, datetime, datetime | None]:
    window = policy.preferred_publish_windows[0] if policy.preferred_publish_windows else {}
    timezone_name = str(window.get("timezone") or policy.primary_timezone)
    target_tz = ZoneInfo(timezone_name)
    now = datetime.now(target_tz)
    day_name = str(window.get("day_of_week") or (policy.publish_days[0] if policy.publish_days else "")).upper()
    target_day = DAYS.get(day_name, now.weekday())
    start = _parse_hhmm(str(window.get("local_time_start") or "09:00"))
    days_ahead = (target_day - now.weekday()) % 7
    candidate_date = now.date() + timedelta(days=days_ahead)
    local_dt = datetime.combine(candidate_date, start, tzinfo=target_tz)
    if local_dt <= now:
        local_dt += timedelta(days=7 if day_name else 1)
    utc_dt = local_dt.astimezone(UTC)
    operator_dt = utc_dt.astimezone(ZoneInfo(policy.operator_timezone)) if policy.operator_timezone else None
    return local_dt, utc_dt, operator_dt


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def _audit(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    reason_code: str,
    payload: dict[str, Any],
    company_id: uuid.UUID | None = None,
) -> None:
    AuditService(session).append(
        AuditEnvelope(
            actor_type="system",
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason_code=reason_code,
            correlation_id="m11-1",
            payload=payload,
        ),
        company_id=company_id,
    )


def _event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    company_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> None:
    DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id="m11-1",
            payload=payload,
        ),
        company_id=company_id,
    )

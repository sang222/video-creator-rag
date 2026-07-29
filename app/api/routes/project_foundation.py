from fastapi import APIRouter, Request

from app.core.errors import ForbiddenError
from app.services.security_boundary import actor_from_request

from app.api.routes.imports import (
    Any,
    CategoryCreativeDigestCreate,
    CategoryCreativeDigestRead,
    CharacterBindingCreate,
    CharacterBindingRead,
    CharacterImageBranchCreate,
    CharacterImageBranchRead,
    CharacterProfileCreate,
    CharacterProfileRead,
    CharacterReferenceAssetCreate,
    CharacterReferenceAssetPackCreate,
    CharacterReferenceAssetPackRead,
    CharacterReferenceAssetRead,
    CharacterVersionCreate,
    CharacterVersionRead,
    ContentCategoryCreate,
    ContentCategoryRead,
    LocalizationReadinessGateRead,
    LocalizationReadinessGateService,
    LocalizedMetadataPackageCreate,
    LocalizedMetadataPackageRead,
    LocalizedMetadataPackageService,
    LocalizedSubtitlePackageCreate,
    LocalizedSubtitlePackageRead,
    LocalizedSubtitlePackageService,
    NotFoundError,
    R3D1AdminService,
    ValidationFailureError,
    VideoProjectCreate,
    VideoProjectLocalizationRead,
    VideoProjectRead,
    VideoProjectService,
    VoiceProfileCreate,
    VoiceProfileRead,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/video-projects", response_model=VideoProjectRead)
    def create_video_project(data: VideoProjectCreate) -> VideoProjectRead:
        try:
            del data
            raise ValidationFailureError(
                "V2_PROJECT_ADMISSION_REQUIRED"
            )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{project_id}/workflow-state")
    def inspect_video_project_workflow(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return VideoProjectService(session).inspect_workflow_state(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/content-categories", response_model=ContentCategoryRead)
    def create_content_category(data: ContentCategoryCreate) -> ContentCategoryRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_content_category(data)
                return ContentCategoryRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/content-categories", response_model=list[ContentCategoryRead])
    def list_content_categories(
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> list[ContentCategoryRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_content_categories(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                )
                return [ContentCategoryRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/content-categories/{category_id}", response_model=ContentCategoryRead)
    def get_content_category(category_id: uuid.UUID) -> ContentCategoryRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_content_category(category_id)
                if record is None:
                    raise NotFoundError(f"content category not found: {category_id}")
                return ContentCategoryRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/category-creative-digests", response_model=CategoryCreativeDigestRead)
    def create_category_creative_digest(data: CategoryCreativeDigestCreate) -> CategoryCreativeDigestRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_category_creative_digest(data)
                return CategoryCreativeDigestRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/character-profiles", response_model=CharacterProfileRead)
    def create_character_profile(data: CharacterProfileCreate) -> CharacterProfileRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_character_profile(data)
                return CharacterProfileRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-profiles", response_model=list[CharacterProfileRead])
    def list_character_profiles(
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> list[CharacterProfileRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_character_profiles(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                )
                return [CharacterProfileRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-profiles/{character_profile_id}", response_model=CharacterProfileRead)
    def get_character_profile(character_profile_id: uuid.UUID) -> CharacterProfileRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_character_profile(character_profile_id)
                if record is None:
                    raise NotFoundError(f"character profile not found: {character_profile_id}")
                return CharacterProfileRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/character-versions", response_model=CharacterVersionRead)
    def create_character_version(data: CharacterVersionCreate) -> CharacterVersionRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_character_version(data)
                return CharacterVersionRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-versions", response_model=list[CharacterVersionRead])
    def list_character_versions(character_profile_id: uuid.UUID | None = None) -> list[CharacterVersionRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_character_versions(character_profile_id=character_profile_id)
                return [CharacterVersionRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-versions/{character_version_id}", response_model=CharacterVersionRead)
    def get_character_version(character_version_id: uuid.UUID) -> CharacterVersionRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_character_version(character_version_id)
                if record is None:
                    raise NotFoundError(f"character version not found: {character_version_id}")
                return CharacterVersionRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/character-image-branches", response_model=CharacterImageBranchRead)
    def create_character_image_branch(data: CharacterImageBranchCreate) -> CharacterImageBranchRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_character_image_branch(data)
                return CharacterImageBranchRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-image-branches", response_model=list[CharacterImageBranchRead])
    def list_character_image_branches(character_version_id: uuid.UUID | None = None) -> list[CharacterImageBranchRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_character_image_branches(character_version_id=character_version_id)
                return [CharacterImageBranchRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-image-branches/{branch_id}", response_model=CharacterImageBranchRead)
    def get_character_image_branch(branch_id: uuid.UUID) -> CharacterImageBranchRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_character_image_branch(branch_id)
                if record is None:
                    raise NotFoundError(f"character image branch not found: {branch_id}")
                return CharacterImageBranchRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/character-reference-asset-packs", response_model=CharacterReferenceAssetPackRead)
    def create_character_reference_asset_pack(
        data: CharacterReferenceAssetPackCreate,
    ) -> CharacterReferenceAssetPackRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_character_reference_asset_pack(data)
                return CharacterReferenceAssetPackRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-reference-asset-packs", response_model=list[CharacterReferenceAssetPackRead])
    def list_character_reference_asset_packs(
        character_image_branch_id: uuid.UUID | None = None,
    ) -> list[CharacterReferenceAssetPackRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_character_reference_asset_packs(
                    character_image_branch_id=character_image_branch_id,
                )
                return [CharacterReferenceAssetPackRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-reference-asset-packs/{pack_id}", response_model=CharacterReferenceAssetPackRead)
    def get_character_reference_asset_pack(pack_id: uuid.UUID) -> CharacterReferenceAssetPackRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_character_reference_asset_pack(pack_id)
                if record is None:
                    raise NotFoundError(f"character reference asset pack not found: {pack_id}")
                return CharacterReferenceAssetPackRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/character-reference-assets", response_model=CharacterReferenceAssetRead)
    def create_character_reference_asset(data: CharacterReferenceAssetCreate) -> CharacterReferenceAssetRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_character_reference_asset(data)
                return CharacterReferenceAssetRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-reference-assets", response_model=list[CharacterReferenceAssetRead])
    def list_character_reference_assets(
        reference_asset_pack_id: uuid.UUID | None = None,
    ) -> list[CharacterReferenceAssetRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_character_reference_assets(
                    reference_asset_pack_id=reference_asset_pack_id,
                )
                return [CharacterReferenceAssetRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-reference-assets/{asset_id}", response_model=CharacterReferenceAssetRead)
    def get_character_reference_asset(asset_id: uuid.UUID) -> CharacterReferenceAssetRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_character_reference_asset(asset_id)
                if record is None:
                    raise NotFoundError(f"character reference asset not found: {asset_id}")
                return CharacterReferenceAssetRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/voice-profiles", response_model=VoiceProfileRead)
    def create_voice_profile(data: VoiceProfileCreate) -> VoiceProfileRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_voice_profile(data)
                return VoiceProfileRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/voice-profiles", response_model=list[VoiceProfileRead])
    def list_voice_profiles(
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> list[VoiceProfileRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_voice_profiles(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                )
                return [VoiceProfileRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/voice-profiles/{voice_profile_id}", response_model=VoiceProfileRead)
    def get_voice_profile(voice_profile_id: uuid.UUID) -> VoiceProfileRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_voice_profile(voice_profile_id)
                if record is None:
                    raise NotFoundError(f"voice profile not found: {voice_profile_id}")
                return VoiceProfileRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/character-bindings", response_model=CharacterBindingRead)
    def create_character_binding(data: CharacterBindingCreate) -> CharacterBindingRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).create_character_binding(data)
                return CharacterBindingRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-bindings", response_model=list[CharacterBindingRead])
    def list_character_bindings(
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
        content_category_id: uuid.UUID | None = None,
    ) -> list[CharacterBindingRead]:
        try:
            with session_scope() as session:
                records = R3D1AdminService(session).list_character_bindings(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                    content_category_id=content_category_id,
                )
                return [CharacterBindingRead.model_validate(record) for record in records]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/character-bindings/{binding_id}", response_model=CharacterBindingRead)
    def get_character_binding(binding_id: uuid.UUID) -> CharacterBindingRead:
        try:
            with session_scope() as session:
                record = R3D1AdminService(session).get_character_binding(binding_id)
                if record is None:
                    raise NotFoundError(f"character binding not found: {binding_id}")
                return CharacterBindingRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{video_project_id}/localization", response_model=VideoProjectLocalizationRead)
    def get_video_project_localization(video_project_id: uuid.UUID) -> VideoProjectLocalizationRead:
        try:
            with session_scope() as session:
                return LocalizationReadinessGateService(session).video_localization(video_project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/video-projects/{video_project_id}/localized-subtitles", response_model=LocalizedSubtitlePackageRead)
    def create_localized_subtitle_package(
        video_project_id: uuid.UUID,
        data: LocalizedSubtitlePackageCreate,
        request: Request,
    ) -> LocalizedSubtitlePackageRead:
        try:
            actor = actor_from_request(request)
            final_review = (
                data.translation_status == "APPROVED"
                or data.human_review_status in {"APPROVED", "NOT_REQUIRED"}
            )
            required_permission = (
                "review.final_decide" if final_review else "editorial.manage"
            )
            if not actor.has_permission(required_permission):
                raise ForbiddenError("Bạn chưa có quyền hoàn tất duyệt localization.")
            if actor.operator_user_id is None:
                raise ForbiddenError("Phiên duyệt không có định danh operator.")
            authoritative = data.model_copy(
                update={"reviewer_id": actor.operator_user_id}
            )
            with session_scope() as session:
                return LocalizedSubtitlePackageService(session).create(
                    video_project_id, authoritative
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/video-projects/{video_project_id}/localized-metadata", response_model=LocalizedMetadataPackageRead)
    def create_localized_metadata_package(
        video_project_id: uuid.UUID,
        data: LocalizedMetadataPackageCreate,
        request: Request,
    ) -> LocalizedMetadataPackageRead:
        try:
            actor = actor_from_request(request)
            required_permission = (
                "review.final_decide"
                if data.human_review_status == "APPROVED"
                else "editorial.manage"
            )
            if not actor.has_permission(required_permission):
                raise ForbiddenError("Bạn chưa có quyền hoàn tất duyệt localization.")
            if actor.operator_user_id is None:
                raise ForbiddenError("Phiên duyệt không có định danh operator.")
            authoritative = data.model_copy(
                update={"reviewer_id": actor.operator_user_id}
            )
            with session_scope() as session:
                return LocalizedMetadataPackageService(session).create(
                    video_project_id, authoritative
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/video-projects/{video_project_id}/localization-readiness/check", response_model=LocalizationReadinessGateRead)
    def check_video_project_localization_readiness(video_project_id: uuid.UUID) -> LocalizationReadinessGateRead:
        try:
            with session_scope() as session:
                return LocalizationReadinessGateService(session).check(video_project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router

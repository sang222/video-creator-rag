import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.contracts.r3d1 import (
    CategoryCreativeDigestCreate,
    CharacterBindingCreate,
    CharacterImageBranchCreate,
    CharacterPolicyMode,
    CharacterProfileCreate,
    CharacterReferenceAssetCreate,
    CharacterReferenceAssetPackCreate,
    CharacterVersionCreate,
    ContentCategoryCreate,
    RuntimeScopeErrorCode,
    VoiceProfileCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    CategoryCreativeDigest,
    ChannelDailyRun,
    ChannelProfileVersion,
    ChannelWorkspace,
    CharacterBinding,
    CharacterImageBranch,
    CharacterProfile,
    CharacterReferenceAsset,
    CharacterReferenceAssetPack,
    CharacterVersion,
    CloudMediaRef,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    EditorialCalendarSlot,
    VoiceProfile,
)
from app.services.channel_contract import CONTRACT_COMPLETE, contract_status_from_snapshot_payload
from app.services.config_registry import content_hash


ACTIVE = "ACTIVE"
NO_CHARACTER = CharacterPolicyMode.NO_CHARACTER.value
OPTIONAL_CHARACTER = CharacterPolicyMode.OPTIONAL_CHARACTER.value
REQUIRED_CHARACTER = CharacterPolicyMode.REQUIRED_CHARACTER.value


def _code(code: RuntimeScopeErrorCode) -> str:
    return code.value


def _db_payload(data: Any) -> dict[str, Any]:
    payload = data.model_dump()
    for key, value in list(payload.items()):
        if isinstance(value, Enum):
            payload[key] = value.value
    return payload


def _hash_payload(data: Any) -> dict[str, Any]:
    return data.model_dump(mode="json")


@dataclass(frozen=True)
class ChannelContractAuthorityResolution:
    channel: ChannelWorkspace | None
    profile_version: ChannelProfileVersion | None
    policy_snapshot: CompiledChannelPolicySnapshot | None
    channel_contract_json: dict[str, Any] | None
    channel_contract_content_hash: str | None
    policy_snapshot_content_hash: str | None
    contract_status: str
    reason_codes: list[str]

    @property
    def ok(self) -> bool:
        return not self.reason_codes

    def as_ref(self) -> dict[str, Any]:
        return {
            "gate": "channel_runtime_authority",
            "ok": self.ok,
            "reason_codes": self.reason_codes,
            "channel_profile_version_id": str(self.profile_version.id) if self.profile_version else None,
            "policy_snapshot_id": str(self.policy_snapshot.id) if self.policy_snapshot else None,
            "channel_contract_content_hash": self.channel_contract_content_hash,
            "policy_snapshot_content_hash": self.policy_snapshot_content_hash,
            "contract_status": self.contract_status,
        }


@dataclass(frozen=True)
class CategoryScopeResolution:
    category: ContentCategory | None
    reason_codes: list[str]
    source: str

    @property
    def ok(self) -> bool:
        return self.category is not None and not self.reason_codes

    def as_ref(self) -> dict[str, Any]:
        return {
            "gate": "category_scope",
            "ok": self.ok,
            "reason_codes": self.reason_codes,
            "category_id": str(self.category.id) if self.category else None,
            "source": self.source,
        }


@dataclass(frozen=True)
class CharacterBindingResolution:
    character_binding: CharacterBinding | None
    reason_codes: list[str]
    source: str

    @property
    def ok(self) -> bool:
        return not self.reason_codes

    def as_ref(self) -> dict[str, Any]:
        return {
            "gate": "character_binding",
            "ok": self.ok,
            "reason_codes": self.reason_codes,
            "character_binding_id": str(self.character_binding.id) if self.character_binding else None,
            "source": self.source,
        }


@dataclass(frozen=True)
class ProjectScopeAdmissionResult:
    authority: ChannelContractAuthorityResolution
    category_scope: CategoryScopeResolution
    character_binding: CharacterBindingResolution

    @property
    def ok(self) -> bool:
        return self.authority.ok and self.category_scope.ok and self.character_binding.ok

    @property
    def reason_codes(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for code in [
            *self.authority.reason_codes,
            *self.category_scope.reason_codes,
            *self.character_binding.reason_codes,
        ]:
            if code not in seen:
                seen.add(code)
                ordered.append(code)
        return ordered

    def readiness_refs(self) -> list[dict[str, Any]]:
        return [
            self.authority.as_ref(),
            self.category_scope.as_ref(),
            self.character_binding.as_ref(),
        ]

    @property
    def category_id(self) -> uuid.UUID | None:
        return self.category_scope.category.id if self.category_scope.category else None

    @property
    def character_binding_id(self) -> uuid.UUID | None:
        return self.character_binding.character_binding.id if self.character_binding.character_binding else None

    @property
    def channel_contract_content_hash(self) -> str | None:
        return self.authority.channel_contract_content_hash


class ChannelRuntimeAuthorityService:
    def __init__(self, session: Session):
        self.session = session

    def resolve(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        policy_snapshot_id: uuid.UUID | None,
    ) -> ChannelContractAuthorityResolution:
        channel = self.session.get(ChannelWorkspace, channel_workspace_id)
        if channel is None or channel.company_id != company_id:
            return self._blocked(
                channel=channel,
                reason_codes=[_code(RuntimeScopeErrorCode.POLICY_SNAPSHOT_MISSING)],
            )
        if policy_snapshot_id is None:
            return self._blocked(
                channel=channel,
                reason_codes=[_code(RuntimeScopeErrorCode.POLICY_SNAPSHOT_MISSING)],
            )
        snapshot = self.session.get(CompiledChannelPolicySnapshot, policy_snapshot_id)
        if (
            snapshot is None
            or snapshot.channel_workspace_id != channel_workspace_id
            or channel.active_policy_snapshot_id != snapshot.id
        ):
            return self._blocked(
                channel=channel,
                policy_snapshot=snapshot,
                reason_codes=[_code(RuntimeScopeErrorCode.POLICY_SNAPSHOT_MISSING)],
            )
        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        if profile_version is None or profile_version.channel_workspace_id != channel_workspace_id:
            return self._blocked(
                channel=channel,
                policy_snapshot=snapshot,
                reason_codes=[_code(RuntimeScopeErrorCode.POLICY_SNAPSHOT_MISSING)],
            )
        payload = snapshot.compiled_payload if isinstance(snapshot.compiled_payload, dict) else {}
        contract = payload.get("channel_contract_json") if isinstance(payload.get("channel_contract_json"), dict) else None
        contract_status, _, _ = contract_status_from_snapshot_payload(payload)
        contract_hash = content_hash(contract) if isinstance(contract, dict) else None
        reasons: list[str] = []
        if snapshot.status != "active" or contract_status != CONTRACT_COMPLETE or contract_hash is None:
            reasons.append(_code(RuntimeScopeErrorCode.CHANNEL_CONTRACT_NOT_COMPLETE))
        return ChannelContractAuthorityResolution(
            channel=channel,
            profile_version=profile_version,
            policy_snapshot=snapshot,
            channel_contract_json=contract,
            channel_contract_content_hash=contract_hash,
            policy_snapshot_content_hash=snapshot.content_hash,
            contract_status=contract_status,
            reason_codes=reasons,
        )

    def _blocked(
        self,
        *,
        channel: ChannelWorkspace | None,
        reason_codes: list[str],
        policy_snapshot: CompiledChannelPolicySnapshot | None = None,
    ) -> ChannelContractAuthorityResolution:
        return ChannelContractAuthorityResolution(
            channel=channel,
            profile_version=None,
            policy_snapshot=policy_snapshot,
            channel_contract_json=None,
            channel_contract_content_hash=None,
            policy_snapshot_content_hash=policy_snapshot.content_hash if policy_snapshot else None,
            contract_status="MISSING",
            reason_codes=reason_codes,
        )


class CategoryScopeResolver:
    def __init__(self, session: Session):
        self.session = session

    def resolve_for_daily_run(
        self,
        daily_run: ChannelDailyRun,
        *,
        explicit_category_id: uuid.UUID | None = None,
    ) -> CategoryScopeResolution:
        slot = self.session.get(EditorialCalendarSlot, daily_run.editorial_calendar_slot_id) if daily_run.editorial_calendar_slot_id else None
        category_id = explicit_category_id or (slot.category_id if slot is not None else None)
        if category_id is not None:
            return self.resolve_explicit(
                company_id=daily_run.company_id,
                channel_workspace_id=daily_run.channel_workspace_id,
                category_id=category_id,
                source="request" if explicit_category_id else "editorial_calendar_slot",
            )
        active_categories = list(
            self.session.scalars(
                select(ContentCategory)
                .where(ContentCategory.company_id == daily_run.company_id)
                .where(ContentCategory.channel_workspace_id == daily_run.channel_workspace_id)
                .where(ContentCategory.status == ACTIVE)
                .order_by(ContentCategory.created_at.asc())
            ).all()
        )
        if len(active_categories) == 1:
            return CategoryScopeResolution(category=active_categories[0], reason_codes=[], source="single_active_auto_bind")
        return CategoryScopeResolution(
            category=None,
            reason_codes=[_code(RuntimeScopeErrorCode.CATEGORY_SCOPE_MISSING)],
            source="missing_or_ambiguous",
        )

    def resolve_explicit(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        category_id: uuid.UUID,
        source: str = "explicit",
    ) -> CategoryScopeResolution:
        category = self.session.get(ContentCategory, category_id)
        if category is None or category.company_id != company_id or category.channel_workspace_id != channel_workspace_id:
            return CategoryScopeResolution(
                category=None,
                reason_codes=[_code(RuntimeScopeErrorCode.CATEGORY_SCOPE_MISSING)],
                source=source,
            )
        if category.status != ACTIVE:
            return CategoryScopeResolution(
                category=category,
                reason_codes=[_code(RuntimeScopeErrorCode.CATEGORY_NOT_ACTIVE)],
                source=source,
            )
        return CategoryScopeResolution(category=category, reason_codes=[], source=source)


class CharacterBindingResolver:
    def __init__(self, session: Session):
        self.session = session

    def resolve(
        self,
        *,
        category: ContentCategory,
        explicit_character_binding_id: uuid.UUID | None = None,
    ) -> CharacterBindingResolution:
        mode = category.character_policy_mode
        if mode == NO_CHARACTER:
            if explicit_character_binding_id is not None:
                return CharacterBindingResolution(
                    character_binding=None,
                    reason_codes=[_code(RuntimeScopeErrorCode.CHARACTER_BINDING_FORBIDDEN)],
                    source="forbidden_by_category",
                )
            return CharacterBindingResolution(character_binding=None, reason_codes=[], source="no_character")
        if explicit_character_binding_id is not None:
            binding = self.session.get(CharacterBinding, explicit_character_binding_id)
            return self._verify_binding(category=category, binding=binding, source="explicit")
        if mode == OPTIONAL_CHARACTER:
            return CharacterBindingResolution(character_binding=None, reason_codes=[], source="optional_not_bound")
        binding = self._find_required_binding(category)
        if binding is None:
            return CharacterBindingResolution(
                character_binding=None,
                reason_codes=[_code(RuntimeScopeErrorCode.CHARACTER_REQUIRED_BUT_MISSING)],
                source="required_missing",
            )
        return self._verify_binding(category=category, binding=binding, source="required_auto_bind")

    def _find_required_binding(self, category: ContentCategory) -> CharacterBinding | None:
        category_bindings = list(
            self.session.scalars(
                select(CharacterBinding)
                .where(CharacterBinding.company_id == category.company_id)
                .where(CharacterBinding.channel_workspace_id == category.channel_workspace_id)
                .where(CharacterBinding.content_category_id == category.id)
                .where(CharacterBinding.status == ACTIVE)
                .order_by(CharacterBinding.created_at.asc())
            ).all()
        )
        if len(category_bindings) == 1:
            return category_bindings[0]
        if len(category_bindings) > 1:
            return None
        channel_bindings = list(
            self.session.scalars(
                select(CharacterBinding)
                .where(CharacterBinding.company_id == category.company_id)
                .where(CharacterBinding.channel_workspace_id == category.channel_workspace_id)
                .where(CharacterBinding.content_category_id.is_(None))
                .where(CharacterBinding.binding_scope == "CHANNEL")
                .where(CharacterBinding.status == ACTIVE)
                .order_by(CharacterBinding.created_at.asc())
            ).all()
        )
        return channel_bindings[0] if len(channel_bindings) == 1 else None

    def _verify_binding(
        self,
        *,
        category: ContentCategory,
        binding: CharacterBinding | None,
        source: str,
    ) -> CharacterBindingResolution:
        if (
            binding is None
            or binding.company_id != category.company_id
            or binding.channel_workspace_id != category.channel_workspace_id
            or binding.status != ACTIVE
            or (binding.content_category_id is not None and binding.content_category_id != category.id)
        ):
            return CharacterBindingResolution(
                character_binding=binding,
                reason_codes=[_code(RuntimeScopeErrorCode.CHARACTER_BINDING_NOT_ACTIVE)],
                source=source,
            )
        profile = self.session.get(CharacterProfile, binding.character_profile_id)
        version = self.session.get(CharacterVersion, binding.character_version_id)
        if (
            profile is None
            or profile.company_id != category.company_id
            or profile.channel_workspace_id != category.channel_workspace_id
            or profile.status != ACTIVE
            or version is None
            or version.character_profile_id != profile.id
            or version.status != ACTIVE
        ):
            return CharacterBindingResolution(
                character_binding=binding,
                reason_codes=[_code(RuntimeScopeErrorCode.CHARACTER_BINDING_NOT_ACTIVE)],
                source=source,
            )
        if category.character_policy_mode == REQUIRED_CHARACTER:
            readiness_reasons = self._required_readiness_reasons(binding=binding, profile=profile, version=version)
            if readiness_reasons:
                return CharacterBindingResolution(character_binding=binding, reason_codes=readiness_reasons, source=source)
        return CharacterBindingResolution(character_binding=binding, reason_codes=[], source=source)

    def _required_readiness_reasons(
        self,
        *,
        binding: CharacterBinding,
        profile: CharacterProfile,
        version: CharacterVersion,
    ) -> list[str]:
        reasons: list[str] = []
        branch = self.session.get(CharacterImageBranch, binding.character_image_branch_id) if binding.character_image_branch_id else None
        if branch is None or branch.character_version_id != version.id or branch.status != ACTIVE:
            reasons.append(_code(RuntimeScopeErrorCode.CHARACTER_ASSET_PACK_MISSING))
        pack = self.session.get(CharacterReferenceAssetPack, binding.reference_asset_pack_id) if binding.reference_asset_pack_id else None
        if (
            pack is None
            or branch is None
            or pack.character_image_branch_id != branch.id
            or pack.status != ACTIVE
            or pack.rights_status != "SAFE"
            or pack.prompt_safety_state != "PROMPT_SAFE"
        ):
            if _code(RuntimeScopeErrorCode.CHARACTER_ASSET_PACK_MISSING) not in reasons:
                reasons.append(_code(RuntimeScopeErrorCode.CHARACTER_ASSET_PACK_MISSING))
        voice = self.session.get(VoiceProfile, binding.voice_profile_id) if binding.voice_profile_id else None
        if (
            voice is None
            or voice.company_id != profile.company_id
            or voice.channel_workspace_id != profile.channel_workspace_id
            or (voice.character_profile_id is not None and voice.character_profile_id != profile.id)
            or voice.status != ACTIVE
            or voice.consent_status == "BLOCKED"
            or voice.commercial_use_status != "ALLOWED"
        ):
            reasons.append(_code(RuntimeScopeErrorCode.CHARACTER_VOICE_PROFILE_MISSING))
        return reasons


class ProjectScopeAdmissionGuard:
    def __init__(self, session: Session):
        self.session = session

    def evaluate_for_daily_run(
        self,
        daily_run: ChannelDailyRun,
        *,
        explicit_category_id: uuid.UUID | None = None,
        explicit_character_binding_id: uuid.UUID | None = None,
    ) -> ProjectScopeAdmissionResult:
        authority = ChannelRuntimeAuthorityService(self.session).resolve(
            company_id=daily_run.company_id,
            channel_workspace_id=daily_run.channel_workspace_id,
            policy_snapshot_id=daily_run.policy_snapshot_id,
        )
        empty_category = CategoryScopeResolution(category=None, reason_codes=[], source="not_evaluated")
        empty_binding = CharacterBindingResolution(character_binding=None, reason_codes=[], source="not_evaluated")
        if not authority.ok:
            return ProjectScopeAdmissionResult(authority=authority, category_scope=empty_category, character_binding=empty_binding)
        category_scope = CategoryScopeResolver(self.session).resolve_for_daily_run(
            daily_run,
            explicit_category_id=explicit_category_id,
        )
        if not category_scope.ok:
            return ProjectScopeAdmissionResult(authority=authority, category_scope=category_scope, character_binding=empty_binding)
        binding_id = explicit_character_binding_id or self._slot_character_binding_id(daily_run)
        character_binding = CharacterBindingResolver(self.session).resolve(
            category=category_scope.category,
            explicit_character_binding_id=binding_id,
        )
        return ProjectScopeAdmissionResult(
            authority=authority,
            category_scope=category_scope,
            character_binding=character_binding,
        )

    def _slot_character_binding_id(self, daily_run: ChannelDailyRun) -> uuid.UUID | None:
        if daily_run.editorial_calendar_slot_id is None:
            return None
        slot = self.session.get(EditorialCalendarSlot, daily_run.editorial_calendar_slot_id)
        payload = slot.character_binding_policy_json if slot is not None else None
        if not isinstance(payload, dict):
            return None
        value = payload.get("character_binding_id")
        if value in (None, ""):
            return None
        return uuid.UUID(str(value))


class R3D1AdminService:
    def __init__(self, session: Session):
        self.session = session

    def create_content_category(self, data: ContentCategoryCreate) -> ContentCategory:
        self._require_channel_for_company(data.company_id, data.channel_workspace_id)
        payload = _db_payload(data)
        category = ContentCategory(**payload, content_hash=content_hash(_hash_payload(data)))
        self.session.add(category)
        self.session.flush()
        return category

    def list_content_categories(
        self,
        *,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> list[ContentCategory]:
        statement = select(ContentCategory).order_by(ContentCategory.created_at.asc())
        if company_id is not None:
            statement = statement.where(ContentCategory.company_id == company_id)
        if channel_workspace_id is not None:
            statement = statement.where(ContentCategory.channel_workspace_id == channel_workspace_id)
        return list(self.session.scalars(statement).all())

    def get_content_category(self, category_id: uuid.UUID) -> ContentCategory | None:
        return self.session.get(ContentCategory, category_id)

    def create_category_creative_digest(self, data: CategoryCreativeDigestCreate) -> CategoryCreativeDigest:
        self._require(ContentCategory, data.content_category_id, "content category")
        payload = _db_payload(data)
        digest = CategoryCreativeDigest(**payload, digest_hash=content_hash(_hash_payload(data)))
        self.session.add(digest)
        self.session.flush()
        return digest

    def create_character_profile(self, data: CharacterProfileCreate) -> CharacterProfile:
        self._require_channel_for_company(data.company_id, data.channel_workspace_id)
        payload = _db_payload(data)
        profile = CharacterProfile(**payload, content_hash=content_hash(_hash_payload(data)))
        self.session.add(profile)
        self.session.flush()
        return profile

    def create_character_version(self, data: CharacterVersionCreate) -> CharacterVersion:
        self._require(CharacterProfile, data.character_profile_id, "character profile")
        payload = _db_payload(data)
        version = CharacterVersion(**payload, content_hash=content_hash(_hash_payload(data)))
        self.session.add(version)
        self.session.flush()
        return version

    def create_character_image_branch(self, data: CharacterImageBranchCreate) -> CharacterImageBranch:
        self._require(CharacterVersion, data.character_version_id, "character version")
        payload = _db_payload(data)
        branch = CharacterImageBranch(**payload, content_hash=content_hash(_hash_payload(data)))
        self.session.add(branch)
        self.session.flush()
        return branch

    def create_character_reference_asset_pack(
        self,
        data: CharacterReferenceAssetPackCreate,
    ) -> CharacterReferenceAssetPack:
        self._require(CharacterImageBranch, data.character_image_branch_id, "character image branch")
        payload = _db_payload(data)
        pack = CharacterReferenceAssetPack(**payload, content_hash=content_hash(_hash_payload(data)))
        self.session.add(pack)
        self.session.flush()
        return pack

    def create_character_reference_asset(self, data: CharacterReferenceAssetCreate) -> CharacterReferenceAsset:
        self._require(CharacterReferenceAssetPack, data.reference_asset_pack_id, "reference asset pack")
        if data.cloud_media_ref_id is not None:
            self._require(CloudMediaRef, data.cloud_media_ref_id, "cloud media ref")
        asset = CharacterReferenceAsset(**_db_payload(data))
        self.session.add(asset)
        self.session.flush()
        return asset

    def create_voice_profile(self, data: VoiceProfileCreate) -> VoiceProfile:
        self._require_channel_for_company(data.company_id, data.channel_workspace_id)
        if data.character_profile_id is not None:
            profile = self._require(CharacterProfile, data.character_profile_id, "character profile")
            if profile.company_id != data.company_id or profile.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("voice profile character does not belong to channel scope")
        payload = _db_payload(data)
        voice = VoiceProfile(**payload, content_hash=content_hash(_hash_payload(data)))
        self.session.add(voice)
        self.session.flush()
        return voice

    def create_character_binding(self, data: CharacterBindingCreate) -> CharacterBinding:
        self._require_channel_for_company(data.company_id, data.channel_workspace_id)
        if data.content_category_id is not None:
            category = self._require(ContentCategory, data.content_category_id, "content category")
            if category.company_id != data.company_id or category.channel_workspace_id != data.channel_workspace_id:
                raise ValidationFailureError("binding category does not belong to channel scope")
        profile = self._require(CharacterProfile, data.character_profile_id, "character profile")
        version = self._require(CharacterVersion, data.character_version_id, "character version")
        if (
            profile.company_id != data.company_id
            or profile.channel_workspace_id != data.channel_workspace_id
            or version.character_profile_id != profile.id
        ):
            raise ValidationFailureError("binding character version does not belong to channel scope")
        if data.character_image_branch_id is not None:
            branch = self._require(CharacterImageBranch, data.character_image_branch_id, "character image branch")
            if branch.character_version_id != version.id:
                raise ValidationFailureError("binding image branch does not belong to character version")
        if data.reference_asset_pack_id is not None:
            pack = self._require(CharacterReferenceAssetPack, data.reference_asset_pack_id, "reference asset pack")
            if data.character_image_branch_id is not None and pack.character_image_branch_id != data.character_image_branch_id:
                raise ValidationFailureError("binding asset pack does not belong to image branch")
        if data.voice_profile_id is not None:
            voice = self._require(VoiceProfile, data.voice_profile_id, "voice profile")
            if (
                voice.company_id != data.company_id
                or voice.channel_workspace_id != data.channel_workspace_id
                or (voice.character_profile_id is not None and voice.character_profile_id != profile.id)
            ):
                raise ValidationFailureError("binding voice profile does not belong to character scope")
        payload = _db_payload(data)
        binding = CharacterBinding(**payload, content_hash=content_hash(_hash_payload(data)))
        self.session.add(binding)
        self.session.flush()
        return binding

    def list_character_profiles(
        self,
        *,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> list[CharacterProfile]:
        return self._list_scoped(CharacterProfile, company_id=company_id, channel_workspace_id=channel_workspace_id)

    def list_character_versions(self, *, character_profile_id: uuid.UUID | None = None) -> list[CharacterVersion]:
        statement = select(CharacterVersion).order_by(CharacterVersion.created_at.asc())
        if character_profile_id is not None:
            statement = statement.where(CharacterVersion.character_profile_id == character_profile_id)
        return list(self.session.scalars(statement).all())

    def list_character_image_branches(self, *, character_version_id: uuid.UUID | None = None) -> list[CharacterImageBranch]:
        statement = select(CharacterImageBranch).order_by(CharacterImageBranch.created_at.asc())
        if character_version_id is not None:
            statement = statement.where(CharacterImageBranch.character_version_id == character_version_id)
        return list(self.session.scalars(statement).all())

    def list_character_reference_asset_packs(
        self,
        *,
        character_image_branch_id: uuid.UUID | None = None,
    ) -> list[CharacterReferenceAssetPack]:
        statement = select(CharacterReferenceAssetPack).order_by(CharacterReferenceAssetPack.created_at.asc())
        if character_image_branch_id is not None:
            statement = statement.where(CharacterReferenceAssetPack.character_image_branch_id == character_image_branch_id)
        return list(self.session.scalars(statement).all())

    def list_character_reference_assets(
        self,
        *,
        reference_asset_pack_id: uuid.UUID | None = None,
    ) -> list[CharacterReferenceAsset]:
        statement = select(CharacterReferenceAsset).order_by(CharacterReferenceAsset.created_at.asc())
        if reference_asset_pack_id is not None:
            statement = statement.where(CharacterReferenceAsset.reference_asset_pack_id == reference_asset_pack_id)
        return list(self.session.scalars(statement).all())

    def list_voice_profiles(
        self,
        *,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> list[VoiceProfile]:
        return self._list_scoped(VoiceProfile, company_id=company_id, channel_workspace_id=channel_workspace_id)

    def list_character_bindings(
        self,
        *,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
        content_category_id: uuid.UUID | None = None,
    ) -> list[CharacterBinding]:
        statement = select(CharacterBinding).order_by(CharacterBinding.created_at.asc())
        if company_id is not None:
            statement = statement.where(CharacterBinding.company_id == company_id)
        if channel_workspace_id is not None:
            statement = statement.where(CharacterBinding.channel_workspace_id == channel_workspace_id)
        if content_category_id is not None:
            statement = statement.where(
                or_(
                    CharacterBinding.content_category_id == content_category_id,
                    CharacterBinding.content_category_id.is_(None),
                )
            )
        return list(self.session.scalars(statement).all())

    def get_character_profile(self, record_id: uuid.UUID) -> CharacterProfile | None:
        return self.session.get(CharacterProfile, record_id)

    def get_character_version(self, record_id: uuid.UUID) -> CharacterVersion | None:
        return self.session.get(CharacterVersion, record_id)

    def get_character_image_branch(self, record_id: uuid.UUID) -> CharacterImageBranch | None:
        return self.session.get(CharacterImageBranch, record_id)

    def get_character_reference_asset_pack(self, record_id: uuid.UUID) -> CharacterReferenceAssetPack | None:
        return self.session.get(CharacterReferenceAssetPack, record_id)

    def get_character_reference_asset(self, record_id: uuid.UUID) -> CharacterReferenceAsset | None:
        return self.session.get(CharacterReferenceAsset, record_id)

    def get_voice_profile(self, record_id: uuid.UUID) -> VoiceProfile | None:
        return self.session.get(VoiceProfile, record_id)

    def get_character_binding(self, record_id: uuid.UUID) -> CharacterBinding | None:
        return self.session.get(CharacterBinding, record_id)

    def _require_channel_for_company(self, company_id: uuid.UUID, channel_workspace_id: uuid.UUID) -> ChannelWorkspace:
        channel = self.session.get(ChannelWorkspace, channel_workspace_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_workspace_id}")
        if channel.company_id != company_id:
            raise ValidationFailureError("channel does not belong to company")
        return channel

    def _require(self, model: Any, record_id: uuid.UUID, label: str) -> Any:
        record = self.session.get(model, record_id)
        if record is None:
            raise NotFoundError(f"{label} not found: {record_id}")
        return record

    def _list_scoped(
        self,
        model: Any,
        *,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> list[Any]:
        statement = select(model).order_by(model.created_at.asc())
        if company_id is not None:
            statement = statement.where(model.company_id == company_id)
        if channel_workspace_id is not None:
            statement = statement.where(model.channel_workspace_id == channel_workspace_id)
        return list(self.session.scalars(statement).all())

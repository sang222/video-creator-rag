from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.cwd()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:100]!r}")
    write(path, content.replace(old, new, 1))


def replace_regex(path: str, pattern: str, replacement: str) -> None:
    content = read(path)
    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{path}: regex replacement count={count}: {pattern[:100]!r}")
    write(path, updated)


# ---------------------------------------------------------------------------
# Pydantic contracts: fail closed and expose v3 canonical publication rows.
# ---------------------------------------------------------------------------
replace_once(
    "app/contracts/youtube_delivery.py",
    "from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator",
    "from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator",
)
replace_once(
    "app/contracts/youtube_delivery.py",
    '''    @field_validator("platform_video_id")
    @classmethod
    def complete_requires_video_id(cls, value: str | None, info):
        if info.data.get("state") == "COMPLETE" and not value:
            raise ValueError("complete upload requires platform_video_id")
        return value
''',
    '''    @model_validator(mode="after")
    def complete_requires_video_id(self) -> "ResumableUploadStatus":
        if self.state == "COMPLETE" and not self.platform_video_id:
            raise ValueError("complete upload requires platform_video_id")
        return self
''',
)
replace_once(
    "app/contracts/production_publish.py",
    '''    observed_published_at: AwareDatetime
    observed_duration_seconds: Decimal = Field(gt=0)

    model_config = ConfigDict(extra="forbid")
''',
    '''    observed_published_at: AwareDatetime
    observed_duration_seconds: Decimal = Field(gt=0)
    observed_tags: list[str] | None = None
    observed_category_id: str | None = Field(default=None, min_length=1)
    observed_default_language: str | None = None
    observed_made_for_kids: bool | None = None
    observed_contains_synthetic_media: bool | None = None
    observed_thumbnail_confirmed: bool | None = None
    observed_caption_confirmed: bool | None = None

    model_config = ConfigDict(extra="forbid")
''',
)
replace_once(
    "app/contracts/production_publish.py",
    '''    verification_status: Literal["VERIFIED"]
    analytics_sync_status: Literal["READY"]
    schema_version: Literal["v2"]
    final_review_candidate_id: uuid.UUID
''',
    '''    verification_status: Literal["VERIFIED"]
    analytics_sync_status: Literal["READY"]
    schema_version: Literal["v2", "v3"]
    final_review_candidate_id: uuid.UUID
''',
)

# ---------------------------------------------------------------------------
# Canonical publication verification carries the complete frozen readback.
# ---------------------------------------------------------------------------
replace_once(
    "app/services/production_publish.py",
    '''        observed_public_metadata = {
            "title": data.observed_title,
            "description": data.observed_description,
            "privacy_status": data.observed_privacy_status,
            "duration_seconds": str(data.observed_duration_seconds),
            "platform": data.observed_platform,
            "platform_channel_id": data.observed_platform_channel_id,
            "platform_video_id": data.observed_platform_video_id,
        }
''',
    '''        observed_public_metadata = {
            "title": data.observed_title,
            "description": data.observed_description,
            "privacy_status": data.observed_privacy_status,
            "duration_seconds": str(data.observed_duration_seconds),
            "platform": data.observed_platform,
            "platform_channel_id": data.observed_platform_channel_id,
            "platform_video_id": data.observed_platform_video_id,
            "tags": data.observed_tags,
            "category_id": data.observed_category_id,
            "default_language": data.observed_default_language,
            "made_for_kids": data.observed_made_for_kids,
            "contains_synthetic_media": data.observed_contains_synthetic_media,
            "thumbnail_confirmed": data.observed_thumbnail_confirmed,
            "caption_confirmed": data.observed_caption_confirmed,
        }
''',
)
replace_once(
    "app/services/production_publish.py",
    '''            "published_at": data.observed_published_at,
            "duration_seconds": data.observed_duration_seconds,
        },
''',
    '''            "published_at": data.observed_published_at,
            "duration_seconds": data.observed_duration_seconds,
            "tags": data.observed_tags,
            "category_id": data.observed_category_id,
            "default_language": data.observed_default_language,
            "made_for_kids": data.observed_made_for_kids,
            "contains_synthetic_media": data.observed_contains_synthetic_media,
            "thumbnail_confirmed": data.observed_thumbnail_confirmed,
            "caption_confirmed": data.observed_caption_confirmed,
        },
''',
)

# ---------------------------------------------------------------------------
# YouTube delivery authority, path, upload, purge, series, and Telegram guards.
# ---------------------------------------------------------------------------
helper_anchor = '''def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))
'''
helper_block = helper_anchor + '''


def _publishing_credential_identity_hash(
    credential: YouTubePublishingCredential,
) -> str:
    return _hash(
        {
            "schema_version": "vcos.youtube-publishing-credential.v1",
            "company_id": str(credential.company_id),
            "channel_workspace_id": str(credential.channel_workspace_id),
            "credential_reference_id": str(credential.credential_reference_id),
            "platform_channel_id": credential.platform_channel_id,
            "account_identity": credential.account_identity,
            "oauth_scopes": sorted(str(item) for item in credential.oauth_scopes),
            "capabilities": sorted(str(item) for item in credential.capabilities),
            "public_release_allowed": False,
            "delete_allowed": False,
        }
    )


def _thumbnail_binding_identity_hash(
    binding: ProductionThumbnailBinding,
    *,
    candidate: FinalReviewCandidate,
) -> str:
    return _hash(
        {
            "schema_version": "vcos.production-thumbnail-binding.v1",
            "candidate_id": str(candidate.id),
            "candidate_hash": candidate.candidate_hash,
            "video_project_id": str(candidate.video_project_id),
            "thumbnail_variant_id": (
                str(binding.thumbnail_variant_id) if binding.thumbnail_variant_id else None
            ),
            "source_type": "AI_GENERATED",
            "provider_key": binding.provider_key,
            "provider_effect_ref": binding.provider_effect_ref,
            "provider_effect_hash": binding.provider_effect_hash,
            "file_ref": binding.file_ref,
            "checksum_sha256": binding.checksum_sha256,
            "mime_type": binding.mime_type,
            "size_bytes": binding.size_bytes,
            "width": binding.width,
            "height": binding.height,
            "state": "VERIFIED",
        }
    )


def _private_stage_identity_hash(
    stage: YouTubePrivateStage,
    *,
    decision: FinalVideoDecision,
    candidate: FinalReviewCandidate,
    final_media: FinalMediaRef,
    credential: YouTubePublishingCredential,
    thumbnail: ProductionThumbnailBinding,
) -> str:
    return _hash(
        {
            "schema_version": "vcos.youtube-private-stage.v1",
            "decision_id": str(decision.id),
            "decision_hash": decision.decision_hash,
            "candidate_id": str(candidate.id),
            "candidate_hash": candidate.candidate_hash,
            "final_media_ref_id": str(final_media.id),
            "final_media_checksum": candidate.final_media_hash,
            "publishing_credential_id": str(credential.id),
            "publishing_credential_hash": credential.content_hash,
            "thumbnail_binding_id": str(thumbnail.id),
            "thumbnail_binding_hash": thumbnail.content_hash,
            "caption_ref": stage.caption_ref,
            "caption_hash": stage.caption_hash,
            "staging_metadata_hash": stage.staging_metadata_hash,
            "public_release_expectation_hash": stage.public_release_expectation_hash,
        }
    )


def _validate_public_release_observation(
    *,
    expectation: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> None:
    if expectation.get("manual_release_only") is not True:
        raise ValidationFailureError("PUBLICATION_RECEIPT_RELEASE_AUTHORITY_INVALID")
    required = (
        "title",
        "description",
        "tags",
        "category_id",
        "default_language",
        "made_for_kids",
        "contains_synthetic_media",
        "thumbnail_confirmed",
        "caption_confirmed",
        "privacy_status",
    )
    missing = [key for key in required if key not in observed or observed[key] is None]
    if missing:
        raise ValidationFailureError(
            "PUBLICATION_RECEIPT_OBSERVATION_INCOMPLETE:" + ",".join(sorted(missing))
        )
    mismatches: list[str] = []
    expected_pairs = {
        "title": expectation.get("title"),
        "description": expectation.get("description"),
        "tags": list(expectation.get("tags") or []),
        "category_id": expectation.get("category_id"),
        "default_language": expectation.get("default_language"),
        "made_for_kids": expectation.get("made_for_kids"),
        "contains_synthetic_media": expectation.get("contains_synthetic_media"),
        "thumbnail_confirmed": True,
        "caption_confirmed": True,
        "privacy_status": str(expectation.get("expected_privacy_status") or "").upper(),
    }
    for key, expected in expected_pairs.items():
        actual = observed.get(key)
        if key == "privacy_status":
            actual = str(actual or "").upper()
        elif key == "tags":
            actual = list(actual or [])
        if actual != expected:
            mismatches.append(key)
    if mismatches:
        raise ValidationFailureError(
            "PUBLICATION_RECEIPT_FROZEN_READBACK_MISMATCH:"
            + ",".join(sorted(mismatches))
        )
'''
replace_once("app/services/youtube_delivery.py", helper_anchor, helper_block)

replace_once(
    "app/services/youtube_delivery.py",
    '''    def query_resumable_session(
        self, *, session_uri: str, total_bytes: int
    ) -> ResumableUploadStatus: ...
''',
    '''    def query_resumable_session(
        self, *, access_token: str, session_uri: str, total_bytes: int
    ) -> ResumableUploadStatus: ...
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''        *,
        session_uri: str,
        media_path: Path,
''',
    '''        *,
        access_token: str,
        session_uri: str,
        media_path: Path,
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''    def query_resumable_session(
        self, *, session_uri: str, total_bytes: int
    ) -> ResumableUploadStatus:
        response = self.client.put(
            session_uri,
            headers={"Content-Length": "0", "Content-Range": f"bytes */{total_bytes}"},
        )
''',
    '''    def query_resumable_session(
        self, *, access_token: str, session_uri: str, total_bytes: int
    ) -> ResumableUploadStatus:
        response = self.client.put(
            session_uri,
            headers={
                **self._headers(access_token),
                "Content-Length": "0",
                "Content-Range": f"bytes */{total_bytes}",
            },
        )
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''    def upload_media(
        self,
        *,
        session_uri: str,
        media_path: Path,
''',
    '''    def upload_media(
        self,
        *,
        access_token: str,
        session_uri: str,
        media_path: Path,
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''            return self.query_resumable_session(
                session_uri=session_uri, total_bytes=total_bytes
            )
''',
    '''            return self.query_resumable_session(
                access_token=access_token,
                session_uri=session_uri,
                total_bytes=total_bytes,
            )
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''                headers={
                    "Content-Type": mime_type,
                    "Content-Length": str(remaining),
''',
    '''                headers={
                    **self._headers(access_token),
                    "Content-Type": mime_type,
                    "Content-Length": str(remaining),
''',
)

replace_once(
    "app/services/youtube_delivery.py",
    '''    def get(self, *, secret_ref: str, expected_hash: str) -> str:
        if not secret_ref.startswith("local_file://") or not _is_sha256(expected_hash):
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        path = Path(secret_ref.removeprefix("local_file://")).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.is_symlink():
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        value = json.loads(path.read_text(encoding="utf-8")).get("session_uri")
''',
    '''    def get(self, *, secret_ref: str, expected_hash: str) -> str:
        if not secret_ref.startswith("local_file://") or not _is_sha256(expected_hash):
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        raw_path = Path(secret_ref.removeprefix("local_file://"))
        if raw_path.is_symlink():
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        try:
            path = raw_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID") from exc
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_REF_INVALID")
        if path.stat().st_mode & 0o077:
            raise ValidationFailureError("YOUTUBE_RESUMABLE_SESSION_PERMISSION_INVALID")
        value = json.loads(path.read_text(encoding="utf-8")).get("session_uri")
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError as exc:
            raise NotFoundError("verified media bytes not found") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise ValidationFailureError("YOUTUBE_MEDIA_PATH_REJECTED")
''',
    '''        if target.is_symlink():
            raise ValidationFailureError("YOUTUBE_MEDIA_PATH_REJECTED")
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError as exc:
            raise NotFoundError("verified media bytes not found") from exc
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValidationFailureError("YOUTUBE_MEDIA_PATH_ESCAPE") from exc
        if not resolved.is_file():
            raise ValidationFailureError("YOUTUBE_MEDIA_PATH_REJECTED")
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''            "tags": list(data.tags),
            "made_for_kids": data.made_for_kids,
            "contains_synthetic_media": data.contains_synthetic_media,
            "platform_channel_id": credential.platform_channel_id,
''',
    '''            "tags": list(data.tags),
            "category_id": data.category_id,
            "default_language": language,
            "made_for_kids": data.made_for_kids,
            "contains_synthetic_media": data.contains_synthetic_media,
            "thumbnail_confirmed": True,
            "caption_confirmed": True,
            "platform_channel_id": credential.platform_channel_id,
''',
)

replace_regex(
    "app/services/youtube_delivery.py",
    r'''    def create_publication_receipt\(\n.*?\n    def create_series_playlist_binding\(''',
    '''    def create_publication_receipt(
        self,
        *,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision,
        confirmation: ManualPublishConfirmation,
        observed_metadata: Mapping[str, Any],
        observed_platform_channel_id: str,
        observed_platform_video_id: str,
        observed_video_url: str,
        observed_published_at: datetime,
        verification_evidence_ref: str,
        verification_evidence_hash: str,
    ) -> PublicPublicationReceipt:
        privacy = str(observed_metadata.get("privacy_status") or "").upper()
        if privacy != "PUBLIC":
            raise ValidationFailureError("PUBLICATION_RECEIPT_REQUIRES_PUBLIC_VISIBILITY")
        if (
            decision.final_review_candidate_id != candidate.id
            or decision.final_media_ref_id != candidate.final_media_ref_id
            or decision.company_id != candidate.company_id
            or decision.channel_workspace_id != candidate.channel_workspace_id
            or decision.video_project_id != candidate.video_project_id
            or confirmation.confirmation_state != "VERIFIED"
            or confirmation.final_review_candidate_id != candidate.id
            or confirmation.final_video_decision_id != decision.id
            or confirmation.final_media_ref_id != candidate.final_media_ref_id
            or confirmation.company_id != candidate.company_id
            or confirmation.channel_workspace_id != candidate.channel_workspace_id
            or confirmation.video_project_id != candidate.video_project_id
        ):
            raise ValidationFailureError("PUBLICATION_RECEIPT_AUTHORITY_LINEAGE_MISMATCH")
        stage = self.session.scalar(
            select(YouTubePrivateStage).where(
                YouTubePrivateStage.final_video_decision_id == decision.id
            )
        )
        if stage is not None:
            final_media = self.session.get(FinalMediaRef, stage.final_media_ref_id)
            credential = self.session.get(
                YouTubePublishingCredential, stage.publishing_credential_id
            )
            thumbnail = self.session.get(
                ProductionThumbnailBinding, stage.production_thumbnail_binding_id
            )
            if (
                final_media is None
                or credential is None
                or thumbnail is None
                or stage.state != "PRIVATE_VERIFIED"
                or stage.platform_video_id != observed_platform_video_id
                or stage.public_release_expectation.get("platform_channel_id")
                != observed_platform_channel_id
                or stage.final_review_candidate_id != candidate.id
                or stage.final_video_decision_id != decision.id
                or stage.final_media_ref_id != candidate.final_media_ref_id
                or stage.staging_metadata_hash != _hash(stage.staging_metadata)
                or stage.public_release_expectation_hash
                != _hash(stage.public_release_expectation)
                or credential.content_hash
                != _publishing_credential_identity_hash(credential)
                or thumbnail.content_hash
                != _thumbnail_binding_identity_hash(thumbnail, candidate=candidate)
                or stage.identity_hash
                != _private_stage_identity_hash(
                    stage,
                    decision=decision,
                    candidate=candidate,
                    final_media=final_media,
                    credential=credential,
                    thumbnail=thumbnail,
                )
            ):
                raise ValidationFailureError("PUBLICATION_RECEIPT_STAGE_LINEAGE_MISMATCH")
            _validate_public_release_observation(
                expectation=stage.public_release_expectation,
                observed=observed_metadata,
            )
        payload = {
            "schema_version": "vcos.public-publication-receipt.v1",
            "candidate_id": str(candidate.id),
            "candidate_hash": candidate.candidate_hash,
            "decision_id": str(decision.id),
            "decision_hash": decision.decision_hash,
            "confirmation_id": str(confirmation.id),
            "youtube_private_stage_id": str(stage.id) if stage else None,
            "platform_channel_id": observed_platform_channel_id,
            "platform_video_id": observed_platform_video_id,
            "public_url": observed_video_url,
            "observed_privacy_status": "PUBLIC",
            "observed_published_at": observed_published_at.isoformat(),
            "observed_metadata_hash": _hash(dict(observed_metadata)),
            "verification_evidence_ref": verification_evidence_ref,
            "verification_evidence_hash": verification_evidence_hash,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(PublicPublicationReceipt).where(
                PublicPublicationReceipt.final_video_decision_id == decision.id
            )
        )
        if existing is not None:
            if existing.receipt_hash != digest:
                raise ConflictError("PUBLICATION_RECEIPT_IMMUTABLE_CONFLICT")
            return existing
        receipt = PublicPublicationReceipt(
            id=uuid.uuid5(_PUBLICATION_NAMESPACE, digest),
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            final_review_candidate_id=candidate.id,
            final_video_decision_id=decision.id,
            manual_publish_confirmation_id=confirmation.id,
            youtube_private_stage_id=stage.id if stage else None,
            platform_channel_id=observed_platform_channel_id,
            platform_video_id=observed_platform_video_id,
            public_url=observed_video_url,
            observed_privacy_status="PUBLIC",
            observed_published_at=observed_published_at,
            observed_metadata=dict(observed_metadata),
            observed_metadata_hash=_hash(dict(observed_metadata)),
            verification_evidence_ref=verification_evidence_ref,
            verification_evidence_hash=verification_evidence_hash,
            receipt_hash=digest,
        )
        self.session.add(receipt)
        self.session.flush()
        if stage is not None:
            binding = self.session.scalar(
                select(YouTubeSeriesEpisodeBinding).where(
                    YouTubeSeriesEpisodeBinding.youtube_private_stage_id == stage.id
                )
            )
            if binding is not None:
                binding.public_publication_receipt_id = receipt.id
                binding.state = "PUBLICATION_VERIFIED"
                binding.binding_hash = _hash(
                    {
                        "prior_binding_hash": binding.binding_hash,
                        "public_publication_receipt_id": str(receipt.id),
                        "state": binding.state,
                    }
                )
        return receipt

    def create_series_playlist_binding(''',
)

replace_once(
    "app/services/youtube_delivery.py",
    '''        if plan is None or credential is None or credential.state != "ACTIVE":
            raise ValidationFailureError("YOUTUBE_SERIES_PLAYLIST_AUTHORITY_INVALID")
''',
    '''        if (
            plan is None
            or credential is None
            or credential.state != "ACTIVE"
            or plan.state != "APPROVED"
            or plan.company_id != credential.company_id
            or plan.channel_workspace_id != credential.channel_workspace_id
        ):
            raise ValidationFailureError("YOUTUBE_SERIES_PLAYLIST_AUTHORITY_INVALID")
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''        if binding.public_episode_ordinal is not None:
            if (
''',
    '''        receipt = (
            self.session.get(
                PublicPublicationReceipt, binding.public_publication_receipt_id
            )
            if binding.public_publication_receipt_id is not None
            else None
        )
        if (
            receipt is None
            or receipt.video_project_id != binding.video_project_id
            or receipt.youtube_private_stage_id != binding.youtube_private_stage_id
            or receipt.platform_video_id != binding.youtube_video_id
            or binding.state not in {"PUBLICATION_VERIFIED", "PLAYLIST_BIND_PENDING"}
        ):
            raise ValidationFailureError(
                "PUBLIC_EPISODE_ORDINAL_REQUIRES_VERIFIED_PUBLICATION"
            )
        if binding.public_episode_ordinal is not None:
            if (
''',
)

replace_regex(
    "app/services/youtube_delivery.py",
    r'''    def _load_scope\(self, session: Session, stage_id: uuid\.UUID\):\n.*?\n    def _upload_video\(''',
    '''    def _load_scope(self, session: Session, stage_id: uuid.UUID):
        stage = session.get(YouTubePrivateStage, stage_id)
        if stage is None:
            raise NotFoundError(f"youtube private stage not found: {stage_id}")
        candidate = session.get(FinalReviewCandidate, stage.final_review_candidate_id)
        decision = session.get(FinalVideoDecision, stage.final_video_decision_id)
        final_media = session.get(FinalMediaRef, stage.final_media_ref_id)
        credential = session.get(
            YouTubePublishingCredential, stage.publishing_credential_id
        )
        thumbnail = session.get(
            ProductionThumbnailBinding, stage.production_thumbnail_binding_id
        )
        cloud = (
            session.get(CloudMediaRef, final_media.cloud_media_ref_id)
            if final_media is not None and final_media.cloud_media_ref_id
            else None
        )
        if (
            candidate is None
            or decision is None
            or final_media is None
            or credential is None
            or thumbnail is None
            or credential.state != "ACTIVE"
            or not _REQUIRED_PRIVATE_CAPABILITIES.issubset(set(credential.capabilities))
            or stage.company_id != candidate.company_id
            or stage.channel_workspace_id != candidate.channel_workspace_id
            or stage.video_project_id != candidate.video_project_id
            or decision.final_review_candidate_id != candidate.id
            or decision.final_media_ref_id != final_media.id
            or decision.company_id != candidate.company_id
            or decision.channel_workspace_id != candidate.channel_workspace_id
            or decision.video_project_id != candidate.video_project_id
            or candidate.final_media_ref_id != final_media.id
            or final_media.file_ref != stage.final_media_ref
            or final_media.checksum_sha256 != stage.final_media_checksum
            or candidate.final_media_hash != stage.final_media_checksum
            or credential.company_id != candidate.company_id
            or credential.channel_workspace_id != candidate.channel_workspace_id
            or credential.platform_channel_id != candidate.destination_platform_channel_id
            or credential.account_identity != candidate.destination_account_identity
            or credential.content_hash
            != _publishing_credential_identity_hash(credential)
            or thumbnail.company_id != candidate.company_id
            or thumbnail.channel_workspace_id != candidate.channel_workspace_id
            or thumbnail.video_project_id != candidate.video_project_id
            or thumbnail.final_review_candidate_id != candidate.id
            or thumbnail.state != "VERIFIED"
            or thumbnail.content_hash
            != _thumbnail_binding_identity_hash(thumbnail, candidate=candidate)
            or stage.staging_metadata_hash != _hash(stage.staging_metadata)
            or stage.public_release_expectation_hash
            != _hash(stage.public_release_expectation)
            or stage.identity_hash
            != _private_stage_identity_hash(
                stage,
                decision=decision,
                candidate=candidate,
                final_media=final_media,
                credential=credential,
                thumbnail=thumbnail,
            )
        ):
            raise ValidationFailureError("YOUTUBE_PRIVATE_STAGE_EXECUTION_SCOPE_INVALID")
        token = YouTubeCredentialResolver(session).access_token(credential)
        return stage, candidate, final_media, cloud, credential, thumbnail, token

    def _upload_video(''',
)

replace_once(
    "app/services/youtube_delivery.py",
    '''        with self.session_factory() as session:
            attempt = session.scalar(
                select(YouTubeUploadAttempt)
''',
    '''        with self.session_factory() as session:
            stage_lock = session.scalar(
                select(YouTubePrivateStage)
                .where(YouTubePrivateStage.id == stage_id)
                .with_for_update()
            )
            if stage_lock is None:
                raise NotFoundError(f"youtube private stage not found: {stage_id}")
            attempt = session.scalar(
                select(YouTubeUploadAttempt)
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''                session.add(attempt)
                session.commit()
            elif (
''',
    '''                session.add(attempt)
                session.flush()
            elif (
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''            attempt_id = attempt.id
        with self.session_factory() as session:
''',
    '''            attempt_id = attempt.id
            submit_session = attempt.state == "INTENDED"
            if submit_session:
                attempt.state = "SESSION_SUBMITTED"
                attempt.outcome_certainty = "UNCERTAIN"
                stage_lock.state = "UPLOADING"
            session.commit()
        with self.session_factory() as session:
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''            if attempt.state == "INTENDED":
                # Seal the one allowed ``videos.insert`` intent before the
                # provider receives it. A crash after this commit is
                # deliberately ambiguous and may not create another session.
                attempt.state = "SESSION_SUBMITTED"
                attempt.outcome_certainty = "UNCERTAIN"
                stage.state = "UPLOADING"
                session.commit()
                try:
''',
    '''            if submit_session:
                # The one allowed videos.insert intent was durably sealed in
                # the stage-locked transaction above before the provider call.
                try:
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''            status = self.transport.query_resumable_session(
                session_uri=session_uri, total_bytes=media.size_bytes
            )
''',
    '''            status = self.transport.query_resumable_session(
                access_token=access_token,
                session_uri=session_uri,
                total_bytes=media.size_bytes,
            )
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''                status = self.transport.upload_media(
                    session_uri=session_uri,
''',
    '''                status = self.transport.upload_media(
                    access_token=access_token,
                    session_uri=session_uri,
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''        with self.session_factory() as session:
            receipt = session.scalar(
                select(YouTubeComponentReceipt).where(
''',
    '''        with self.session_factory() as session:
            stage_lock = session.scalar(
                select(YouTubePrivateStage)
                .where(YouTubePrivateStage.id == stage_id)
                .with_for_update()
            )
            if stage_lock is None:
                raise NotFoundError(f"youtube private stage not found: {stage_id}")
            receipt = session.scalar(
                select(YouTubeComponentReceipt).where(
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''                session.add(attempt)
                session.commit()
            elif attempt.request_hash != request_hash:
''',
    '''                session.add(attempt)
                session.flush()
            elif attempt.request_hash != request_hash:
''',
)

replace_regex(
    "app/services/youtube_delivery.py",
    r'''class LocalMediaPurgeExecutor:\n.*?\n\nclass TelegramTransport\(Protocol\):''',
    '''class LocalMediaPurgeExecutor:
    """Remove active MP4 bytes with durable, crash-reconcilable phases."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        allowed_root: Path | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.allowed_root = (allowed_root or _production_root()).resolve()

    def execute(self, *, attempt_id: uuid.UUID) -> LocalMediaPurgeReceipt:
        # Phase 1: persist the one deletion intent before touching bytes.
        with self.session_factory() as session:
            attempt = session.scalar(
                select(LocalMediaPurgeAttempt)
                .where(LocalMediaPurgeAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise NotFoundError(f"local purge attempt not found: {attempt_id}")
            existing = session.scalar(
                select(LocalMediaPurgeReceipt).where(
                    LocalMediaPurgeReceipt.youtube_private_stage_id
                    == attempt.youtube_private_stage_id
                )
            )
            if existing is not None:
                return existing
            stage = session.get(YouTubePrivateStage, attempt.youtube_private_stage_id)
            if stage is None or stage.state != "PRIVATE_VERIFIED":
                raise ValidationFailureError(
                    "LOCAL_MEDIA_PURGE_REQUIRES_PRIVATE_VERIFIED"
                )
            if attempt.state == "INTENDED":
                attempt.state = "SUBMITTED"
                attempt.attempt_count = 1
                attempt.error_code = None
                session.commit()
            elif attempt.state not in {"SUBMITTED", "QUARANTINED", "PURGED"}:
                raise ValidationFailureError("LOCAL_MEDIA_PURGE_STATE_INVALID")

        # Phase 2: serialize quarantine reconciliation on the attempt row.
        with self.session_factory() as session:
            attempt = session.scalar(
                select(LocalMediaPurgeAttempt)
                .where(LocalMediaPurgeAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise NotFoundError(f"local purge attempt not found: {attempt_id}")
            existing = session.scalar(
                select(LocalMediaPurgeReceipt).where(
                    LocalMediaPurgeReceipt.youtube_private_stage_id
                    == attempt.youtube_private_stage_id
                )
            )
            if existing is not None:
                return existing
            original = _purge_path(
                attempt.original_file_ref, root=self.allowed_root, kind="original"
            )
            quarantine = _purge_path(
                attempt.quarantine_file_ref, root=self.allowed_root, kind="quarantine"
            )
            original_exists = original.is_file() and not original.is_symlink()
            quarantine_exists = quarantine.is_file() and not quarantine.is_symlink()
            if original_exists and quarantine_exists:
                raise ValidationFailureError("LOCAL_MEDIA_PURGE_DUPLICATE_BYTES")
            if attempt.state == "SUBMITTED":
                try:
                    if original_exists:
                        if _sha256_file(original) != attempt.checksum_sha256:
                            raise ValidationFailureError(
                                "LOCAL_MEDIA_PURGE_BYTES_MISMATCH"
                            )
                        quarantine.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(original, quarantine)
                        quarantine_exists = True
                    elif not quarantine_exists:
                        attempt.error_code = (
                            "LOCAL_MEDIA_PURGE_RECONCILIATION_REQUIRED"
                        )
                        session.commit()
                        raise ValidationFailureError(attempt.error_code)
                    if _sha256_file(quarantine) != attempt.checksum_sha256:
                        raise ValidationFailureError(
                            "LOCAL_MEDIA_PURGE_QUARANTINE_MISMATCH"
                        )
                except OSError as exc:
                    attempt.error_code = "LOCAL_MEDIA_PURGE_RECONCILIATION_REQUIRED"
                    session.commit()
                    raise ValidationFailureError(attempt.error_code) from exc
                attempt.state = "QUARANTINED"
                attempt.error_code = None
                session.commit()
            elif attempt.state == "QUARANTINED":
                if original_exists:
                    raise ValidationFailureError(
                        "LOCAL_MEDIA_PURGE_ORIGINAL_REAPPEARED"
                    )
                if quarantine_exists and _sha256_file(quarantine) != attempt.checksum_sha256:
                    raise ValidationFailureError(
                        "LOCAL_MEDIA_PURGE_QUARANTINE_MISMATCH"
                    )

        # Phase 3: unlink and atomically persist the receipt. A crash after
        # unlink leaves QUARANTINED durable, so the next invocation can finish.
        with self.session_factory() as session:
            attempt = session.scalar(
                select(LocalMediaPurgeAttempt)
                .where(LocalMediaPurgeAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise NotFoundError(f"local purge attempt not found: {attempt_id}")
            existing = session.scalar(
                select(LocalMediaPurgeReceipt).where(
                    LocalMediaPurgeReceipt.youtube_private_stage_id
                    == attempt.youtube_private_stage_id
                )
            )
            if existing is not None:
                return existing
            if attempt.state != "QUARANTINED":
                raise ValidationFailureError(
                    "LOCAL_MEDIA_PURGE_RECONCILIATION_REQUIRED"
                )
            original = _purge_path(
                attempt.original_file_ref, root=self.allowed_root, kind="original"
            )
            quarantine = _purge_path(
                attempt.quarantine_file_ref, root=self.allowed_root, kind="quarantine"
            )
            if original.exists():
                raise ValidationFailureError("LOCAL_MEDIA_PURGE_ORIGINAL_REAPPEARED")
            try:
                if quarantine.exists():
                    if quarantine.is_symlink() or _sha256_file(quarantine) != attempt.checksum_sha256:
                        raise ValidationFailureError(
                            "LOCAL_MEDIA_PURGE_QUARANTINE_MISMATCH"
                        )
                    quarantine.unlink()
            except OSError as exc:
                attempt.error_code = "LOCAL_MEDIA_PURGE_RECONCILIATION_REQUIRED"
                session.commit()
                raise ValidationFailureError(attempt.error_code) from exc
            if original.exists() or quarantine.exists():
                attempt.error_code = "LOCAL_MEDIA_PURGE_RECONCILIATION_REQUIRED"
                session.commit()
                raise ValidationFailureError(attempt.error_code)
            body = {
                "schema_version": "vcos.local-media-purge-receipt.v1",
                "youtube_private_stage_id": str(attempt.youtube_private_stage_id),
                "final_media_ref_id": str(attempt.final_media_ref_id),
                "local_file_ref": attempt.original_file_ref,
                "checksum_sha256": attempt.checksum_sha256,
                "state": "PURGED",
                "attempt_hash": attempt.content_hash,
            }
            receipt = LocalMediaPurgeReceipt(
                youtube_private_stage_id=attempt.youtube_private_stage_id,
                final_media_ref_id=attempt.final_media_ref_id,
                local_file_ref=attempt.original_file_ref,
                checksum_sha256=attempt.checksum_sha256,
                state="PURGED",
                deleted_at=utc_now(),
                receipt_hash=_hash(body),
            )
            attempt.state = "PURGED"
            attempt.error_code = None
            final_media = session.get(FinalMediaRef, attempt.final_media_ref_id)
            cloud_ref = (
                session.get(CloudMediaRef, final_media.cloud_media_ref_id)
                if final_media is not None and final_media.cloud_media_ref_id is not None
                else None
            )
            if cloud_ref is not None:
                if (
                    cloud_ref.storage_provider != "VCOS_LOCAL_ARCHIVE"
                    or cloud_ref.checksum_sha256 != attempt.checksum_sha256
                ):
                    raise ValidationFailureError(
                        "LOCAL_MEDIA_PURGE_CLOUD_AUTHORITY_DRIFT"
                    )
                cloud_ref.local_cleanup_status = "CLEANED"
                cloud_ref.cleaned_at = receipt.deleted_at
                cloud_ref.retention_policy = {
                    **dict(cloud_ref.retention_policy or {}),
                    "keep_local": False,
                    "cleanup_authority": "YOUTUBE_PRIVATE_VERIFIED",
                    "youtube_private_stage_id": str(
                        attempt.youtube_private_stage_id
                    ),
                }
            session.add(receipt)
            session.commit()
            return receipt


class TelegramTransport(Protocol):''',
)

replace_once(
    "app/services/youtube_delivery.py",
    '''        reference = self.session.scalar(
            select(CredentialReference).where(
                CredentialReference.provider_key == "telegram_bot",
                CredentialReference.status.in_(["CONFIGURED", "ACTIVE"]),
            )
        )
        chat_ref = os.getenv("VCOS_TELEGRAM_CHAT_ID_REF")
''',
    '''        reference = _select_telegram_reference(self.session, candidate=candidate)
        reference_metadata = dict(reference.metadata_ or {}) if reference else {}
        chat_ref = reference_metadata.get("chat_binding_ref") or os.getenv(
            "VCOS_TELEGRAM_CHAT_ID_REF"
        )
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''            if reference is None or not reference.secret_ref or not notice.chat_binding_ref:
                notice.state = "BLOCKED_CONFIG"
''',
    '''            if (
                reference is None
                or not reference.secret_ref
                or not notice.chat_binding_ref
                or not _credential_reference_matches_scope(
                    reference,
                    company_id=notice.company_id,
                    channel_workspace_id=notice.channel_workspace_id,
                )
            ):
                notice.state = "BLOCKED_CONFIG"
''',
)
replace_once(
    "app/services/youtube_delivery.py",
    '''def _resolve_local_ref(value: str, *, root: Path) -> Path:
    raw = value.removeprefix("file://")
    path = Path(raw)
    if not path.is_absolute():
        path = root / raw
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NotFoundError("thumbnail file not found") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise ValidationFailureError("PRODUCTION_THUMBNAIL_PATH_REJECTED")
    return resolved
''',
    '''def _resolve_local_ref(value: str, *, root: Path) -> Path:
    raw = value.removeprefix("file://")
    path = Path(raw)
    if not path.is_absolute():
        path = root / raw
    if path.is_symlink():
        raise ValidationFailureError("PRODUCTION_THUMBNAIL_PATH_REJECTED")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NotFoundError("thumbnail file not found") from exc
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationFailureError("PRODUCTION_THUMBNAIL_PATH_ESCAPE") from exc
    if not resolved.is_file():
        raise ValidationFailureError("PRODUCTION_THUMBNAIL_PATH_REJECTED")
    return resolved
''',
)

secret_anchor = '''def _resolve_simple_secret(secret_ref: str) -> str:
'''
telegram_helpers = '''def _credential_reference_matches_scope(
    reference: CredentialReference,
    *,
    company_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
) -> bool:
    metadata = dict(reference.metadata_ or {})
    if str(metadata.get("company_id") or "") != str(company_id):
        return False
    channel_value = metadata.get("channel_workspace_id")
    return channel_value in {None, "", str(channel_workspace_id)}


def _select_telegram_reference(
    session: Session,
    *,
    candidate: FinalReviewCandidate,
) -> CredentialReference | None:
    references = list(
        session.scalars(
            select(CredentialReference)
            .where(
                CredentialReference.provider_key == "telegram_bot",
                CredentialReference.status.in_(["CONFIGURED", "ACTIVE"]),
            )
            .order_by(CredentialReference.created_at.asc())
        ).all()
    )
    exact: list[CredentialReference] = []
    company_default: list[CredentialReference] = []
    for reference in references:
        metadata = dict(reference.metadata_ or {})
        if str(metadata.get("company_id") or "") != str(candidate.company_id):
            continue
        channel_value = metadata.get("channel_workspace_id")
        if str(channel_value or "") == str(candidate.channel_workspace_id):
            exact.append(reference)
        elif channel_value in {None, ""}:
            company_default.append(reference)
    selected = exact or company_default
    if len(selected) > 1:
        raise ValidationFailureError("TELEGRAM_CREDENTIAL_SCOPE_AMBIGUOUS")
    return selected[0] if selected else None


'''
replace_once(
    "app/services/youtube_delivery.py",
    secret_anchor,
    telegram_helpers + secret_anchor,
)
replace_regex(
    "app/services/youtube_delivery.py",
    r'''def _resolve_simple_secret\(secret_ref: str\) -> str:\n.*?\n\ndef _resolve_chat_binding''',
    '''def _resolve_simple_secret(secret_ref: str) -> str:
    if secret_ref.startswith("env://"):
        value = os.getenv(secret_ref.removeprefix("env://"))
    elif secret_ref.startswith("env:"):
        value = os.getenv(secret_ref.removeprefix("env:"))
    elif secret_ref.startswith("local_file://"):
        raw_path = Path(secret_ref.removeprefix("local_file://"))
        if raw_path.is_symlink():
            raise ValidationFailureError("DELIVERY_SECRET_PATH_REJECTED")
        try:
            path = raw_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValidationFailureError("DELIVERY_SECRET_UNAVAILABLE") from exc
        try:
            path.relative_to(_delivery_secret_root())
        except ValueError as exc:
            raise ValidationFailureError("DELIVERY_SECRET_PATH_ESCAPE") from exc
        if not path.is_file() or path.stat().st_mode & 0o077:
            raise ValidationFailureError("DELIVERY_SECRET_PERMISSION_INVALID")
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("bot_token") or payload.get("access_token")
    else:
        value = None
    if not value:
        raise ValidationFailureError("DELIVERY_SECRET_UNAVAILABLE")
    return str(value)


def _resolve_chat_binding''',
)

# Correct sidecar file naming: final.srt, not final.mp4.srt.
replace_once(
    "app/services/v2_native_effects.py",
    '''        "file_name": f"{cloud.file_name}.srt",
''',
    '''        "file_name": Path(cloud.file_name).with_suffix(".srt").name,
''',
)

# ---------------------------------------------------------------------------
# Regression coverage for the strengthened invariants.
# ---------------------------------------------------------------------------
replace_once(
    "tests/test_youtube_private_delivery.py",
    '''import hashlib
import uuid
from pathlib import Path
''',
    '''import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import get_args
''',
)
replace_once(
    "tests/test_youtube_private_delivery.py",
    '''from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.youtube_delivery import ResumableUploadStatus
''',
    '''from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.production_publish import UploadedVideoReadV2
from app.contracts.youtube_delivery import ResumableUploadStatus
''',
)
replace_once(
    "tests/test_youtube_private_delivery.py",
    '''    LocalSessionSecretStore,
    ResolvedMediaBytes,
    YouTubeDataApiTransport,
    YouTubePrivateStageExecutor,
    _normalize_youtube_readback,
)
''',
    '''    LocalSessionSecretStore,
    ResolvedMediaBytes,
    VerifiedMediaByteSourceResolver,
    YouTubeDataApiTransport,
    YouTubePrivateStageExecutor,
    _normalize_youtube_readback,
    _validate_public_release_observation,
)
''',
)
append_tests = r'''

class _BlockingUploadTransport(_SuccessfulUploadTransport):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def create_resumable_session(self, **_kwargs) -> str:
        self.create_calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("blocking upload transport timed out")
        return "https://upload.youtube.test/session/concurrent"


def test_complete_upload_status_requires_platform_video_id() -> None:
    with pytest.raises(ValueError, match="platform_video_id"):
        ResumableUploadStatus(state="COMPLETE", committed_bytes=1)


def test_uploaded_video_read_contract_accepts_v3_rows() -> None:
    annotation = UploadedVideoReadV2.model_fields["schema_version"].annotation
    assert set(get_args(annotation)) == {"v2", "v3"}


def test_resumable_query_and_upload_send_authorization(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer exact-token"
        if request.headers.get("Content-Range", "").startswith("bytes */"):
            return httpx.Response(308, headers={"Range": "bytes=0-0"})
        return httpx.Response(200, json={"id": "video-authenticated"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = YouTubeDataApiTransport(client=client)
    queried = transport.query_resumable_session(
        access_token="exact-token",
        session_uri="https://upload.youtube.test/session/auth",
        total_bytes=2,
    )
    assert queried.committed_bytes == 1
    media_path = tmp_path / "auth.mp4"
    media_path.write_bytes(b"ab")
    uploaded = transport.upload_media(
        access_token="exact-token",
        session_uri="https://upload.youtube.test/session/auth",
        media_path=media_path,
        start_offset=1,
        total_bytes=2,
        mime_type="video/mp4",
    )
    assert uploaded.platform_video_id == "video-authenticated"
    assert len(requests) == 2


def test_verified_media_resolver_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.srt"
    outside.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")
    checksum = hashlib.sha256(outside.read_bytes()).hexdigest()
    resolver = VerifiedMediaByteSourceResolver(workspace_root=root)
    with pytest.raises(ValidationFailureError, match="YOUTUBE_MEDIA_PATH_ESCAPE"):
        with resolver.resolve_sidecar(
            file_ref=f"file://{outside}",
            expected_checksum=checksum,
            mime_type="application/x-subrip",
        ):
            pass


def test_public_release_observation_must_match_frozen_expectation() -> None:
    expectation = {
        "expected_privacy_status": "PUBLIC",
        "manual_release_only": True,
        "title": "Frozen title",
        "description": "Frozen description",
        "tags": ["one", "two"],
        "category_id": "28",
        "default_language": "en",
        "made_for_kids": False,
        "contains_synthetic_media": True,
        "thumbnail_confirmed": True,
        "caption_confirmed": True,
    }
    observed = {
        "privacy_status": "PUBLIC",
        "title": "Frozen title",
        "description": "Frozen description",
        "tags": ["one", "two"],
        "category_id": "28",
        "default_language": "en",
        "made_for_kids": False,
        "contains_synthetic_media": True,
        "thumbnail_confirmed": True,
        "caption_confirmed": True,
    }
    _validate_public_release_observation(
        expectation=expectation,
        observed=observed,
    )
    with pytest.raises(
        ValidationFailureError,
        match="PUBLICATION_RECEIPT_FROZEN_READBACK_MISMATCH",
    ):
        _validate_public_release_observation(
            expectation=expectation,
            observed={**observed, "tags": ["drifted"]},
        )
    with pytest.raises(
        ValidationFailureError,
        match="PUBLICATION_RECEIPT_OBSERVATION_INCOMPLETE",
    ):
        _validate_public_release_observation(
            expectation=expectation,
            observed={key: value for key, value in observed.items() if key != "category_id"},
        )


def test_concurrent_upload_cannot_create_second_resumable_session(
    db_session: Session,
    tmp_path: Path,
) -> None:
    stage = _insert_stage(db_session, label="concurrent-upload")
    media_path = tmp_path / "concurrent.mp4"
    media_path.write_bytes(b"concurrent-private-video-bytes")
    media = ResolvedMediaBytes(
        path=media_path,
        checksum_sha256=hashlib.sha256(media_path.read_bytes()).hexdigest(),
        size_bytes=media_path.stat().st_size,
        mime_type="video/mp4",
    )
    transport = _BlockingUploadTransport()
    executor = _executor(db_session, transport=transport, tmp_path=tmp_path)
    snapshot = {
        "identity_hash": stage.identity_hash,
        "staging_metadata": stage.staging_metadata,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            executor._upload_video,
            stage_id=stage.id,
            stage_snapshot=snapshot,
            media=media,
            access_token="test-token",
        )
        assert transport.entered.wait(timeout=5)
        with pytest.raises(
            ValidationFailureError,
            match="YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN",
        ):
            executor._upload_video(
                stage_id=stage.id,
                stage_snapshot=snapshot,
                media=media,
                access_token="test-token",
            )
        transport.release.set()
        assert first.result(timeout=15) == "video-private-1"
    assert transport.create_calls == 1


def test_concurrent_component_write_is_fail_closed(
    db_session: Session,
    tmp_path: Path,
) -> None:
    stage = _insert_stage(db_session, label="concurrent-component")
    executor = _executor(
        db_session,
        transport=_SuccessfulUploadTransport(),
        tmp_path=tmp_path,
    )
    entered = Event()
    release = Event()
    calls: list[str] = []

    def call() -> dict[str, str]:
        calls.append("called")
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("blocking component transport timed out")
        return {"etag": "component-etag"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            executor._write_component_once,
            stage_id=stage.id,
            component_type="CAPTION",
            request_payload={"video_id": "video-1", "checksum": "d" * 64},
            call=call,
        )
        assert entered.wait(timeout=5)
        with pytest.raises(
            ValidationFailureError,
            match="YOUTUBE_COMPONENT_OUTCOME_UNKNOWN",
        ):
            executor._write_component_once(
                stage_id=stage.id,
                component_type="CAPTION",
                request_payload={"video_id": "video-1", "checksum": "d" * 64},
                call=lambda: {"etag": "must-not-run"},
            )
        release.set()
        first.result(timeout=15)
    assert calls == ["called"]
'''
test_path = "tests/test_youtube_private_delivery.py"
test_content = read(test_path)
if "test_complete_upload_status_requires_platform_video_id" in test_content:
    raise RuntimeError("tests already patched")
write(test_path, test_content.rstrip() + append_tests + "\n")

# ---------------------------------------------------------------------------
# CI covers every Python surface changed by PR #3.
# ---------------------------------------------------------------------------
replace_once(
    ".github/workflows/youtube-private-delivery.yml",
    '''          app/db/models/m7.py
          app/db/models/youtube_delivery.py
''',
    '''          app/db/models/__init__.py
          app/db/models/m7.py
          app/db/models/youtube_delivery.py
''',
)
replace_once(
    ".github/workflows/youtube-private-delivery.yml",
    '''          app/services/outbox_dispatcher.py
          app/services/security_boundary.py
''',
    '''          app/services/outbox_dispatcher.py
          app/services/pkg1.py
          app/services/production_start_readiness.py
          app/services/runtime_bootstrap.py
          app/services/security_boundary.py
''',
)

# ---------------------------------------------------------------------------
# Consolidate documentation and add the exact local Codex execution prompt.
# ---------------------------------------------------------------------------
for obsolete in (
    "docs/architecture/youtube_private_delivery_final_check.md",
    "docs/architecture/youtube_private_delivery_merge_notes.md",
    "docs/architecture/youtube_private_delivery_pr3.md",
    "docs/architecture/youtube_private_delivery_release.md",
    "docs/architecture/youtube_private_delivery_review.md",
    "docs/architecture/youtube_private_delivery_scope.md",
    "docs/architecture/youtube_private_delivery_status.md",
):
    (ROOT / obsolete).unlink(missing_ok=True)

closeout_path = "docs/architecture/youtube_private_delivery_closeout.md"
closeout = read(closeout_path).rstrip()
closeout_appendix = r'''

## PR #3 code-only closeout audit

The code-only boundary is closed when CI proves all of the following:

- one Alembic head at `0084_youtube_private_delivery`;
- complete upload results cannot exist without a platform video ID;
- every resumable-session query and byte upload is authenticated;
- stage, credential, thumbnail, media, metadata, and release-expectation hashes are recomputed before effects;
- concurrent upload/component workers cannot emit a second provider effect;
- public publication requires complete exact readback of frozen metadata, synthetic-media state, thumbnail, and caption authority;
- series playlist authority cannot cross company/channel scope and public ordinals cannot bind before verified publication;
- local media paths and local secret paths cannot escape their configured roots;
- local purge survives crashes between quarantine, unlink, and receipt persistence;
- Telegram credentials are company/channel scoped and ambiguous bindings fail closed;
- no API path exists for automatic public release or delete.

Live OAuth, provider, Telegram, filesystem, and human-public-release evidence remains a local operator task. Execute it with `docs/operations/youtube_private_delivery_local_codex_master_prompt.md`; never weaken the human publication boundary to make a canary pass.
'''
if "## PR #3 code-only closeout audit" not in closeout:
    closeout += closeout_appendix
write(closeout_path, closeout.rstrip() + "\n")

activation_path = "docs/operations/youtube_private_delivery_activation.md"
activation = read(activation_path).rstrip()
activation_note = r'''

## Local activation authority

The canonical local execution procedure is the master prompt in `youtube_private_delivery_local_codex_master_prompt.md`. The prompt is intentionally evidence-first, forbids public-release API calls, and stops at the human boundary before canonical publication verification.
'''
if "## Local activation authority" not in activation:
    activation += activation_note
write(activation_path, activation.rstrip() + "\n")

master_prompt = r'''# MASTER PROMPT — VCOS PR #3 LOCAL YOUTUBE PRIVATE-DELIVERY CANARY

You are Codex acting as a Principal Production Reliability Engineer, YouTube Data API Integration Engineer, Database/State-Machine Auditor, Security Engineer, and VCOS Operator.

Your task is to execute the local-only closeout for VCOS PR #3 after the code PR is reviewed. This is not an architecture redesign. Repository code, runtime database state, and immutable receipts are the authority.

## 0. Non-negotiable boundaries

1. Work only in `sang222/video-creator-rag` on the reviewed PR #3 head or its merged `main` descendant.
2. Preserve the existing dirty tree. Never reset, clean, stash, or overwrite unrelated operator work.
3. Never print, echo, log, commit, upload, or paste OAuth tokens, refresh tokens, client secrets, Telegram bot tokens, chat IDs, or resumable session URIs.
4. Never call a YouTube API operation that makes a video public, schedules publication, deletes a video, or changes the human-only release boundary.
5. The only permitted YouTube write effects are the exact frozen private upload, thumbnail write, caption write, and optional private playlist operations already authorized by PR #3.
6. Never issue a second `videos.insert` when the first outcome is uncertain. Reconcile the exact persisted resumable session or stop with `YOUTUBE_RESUMABLE_SESSION_OUTCOME_UNKNOWN`.
7. Never use browser automation, account farming, VPN/IP manipulation, fake views, engagement exchange, reupload spam, or metadata spoofing.
8. Never mutate `main` or open/merge another PR unless the operator explicitly asks after reviewing the final evidence report.
9. Do not invent IDs, checksums, database states, provider responses, or success evidence.
10. A local canary is successful only when every claimed state is backed by database rows, hashes, provider readback, and filesystem evidence.

## 1. Establish exact authority

Run and record, with secrets redacted:

```bash
pwd
git status --short
git branch --show-current
git rev-parse HEAD
git log -1 --oneline
```

Requirements:

- repository path must be the operator's VCOS repository;
- branch/head must contain PR #3;
- no unreviewed local code change may be silently included;
- record the initial dirty-tree paths and preserve them.

Inspect:

- `docs/architecture/youtube_private_delivery_closeout.md`
- `docs/architecture/youtube_private_delivery_invariants.md`
- `docs/operations/youtube_private_delivery_activation.md`
- `docs/operations/youtube_private_delivery_failure_modes.md`
- migration `0084_youtube_private_delivery`
- the YouTube delivery service, contracts, routes, outbox dispatch, production publish service, and tests.

Stop if the checked-out code does not preserve `private stage → human public release → verified canonical publication`.

## 2. Local safety preflight

Without revealing values, verify these controls:

- `VCOS_DISABLE_UPLOAD_AND_PUBLISH` is true for all dry-run/test phases;
- `VCOS_DISABLE_MEDIA_PROVIDER_CALLS` is true for all dry-run/test phases;
- `VCOS_V2_PRODUCTION_ROOT` resolves to the expected local production root;
- `VCOS_DELIVERY_SECRET_ROOT` resolves to the dedicated secret root;
- secret files are regular files, not symlinks, live under the configured secret root, and have mode `0600`;
- the database URL points to the intended local VCOS database;
- Docker/PostgreSQL/worker processes are the intended environment, not an unrelated production host.

Do not continue on ambiguous environment identity.

## 3. Build and deterministic validation

Run the repository's supported setup, then:

```bash
alembic upgrade head
alembic heads
pytest -q tests/test_youtube_private_delivery.py \
  tests/test_voice_authority.py \
  tests/test_voice_execution.py \
  tests/test_v2_caption_grouping.py \
  tests/test_cross_modal_lineage.py \
  tests/test_v2_renderer_reconciliation.py \
  tests/test_v2_drive_archive_crash_recovery.py
ruff check --isolated \
  app/contracts/channel_policy.py \
  app/contracts/production_publish.py \
  app/contracts/youtube_delivery.py \
  app/db/models/__init__.py \
  app/db/models/m7.py \
  app/db/models/youtube_delivery.py \
  app/api/routes/youtube_delivery.py \
  app/main.py \
  app/services/youtube_delivery.py \
  app/services/production_publish.py \
  app/services/long_form_analytics.py \
  app/services/m9.py \
  app/services/outbox_dispatcher.py \
  app/services/pkg1.py \
  app/services/production_start_readiness.py \
  app/services/runtime_bootstrap.py \
  app/services/security_boundary.py \
  app/services/v2_native_effects.py \
  app/services/v2_package_readiness.py \
  app/services/v2_provider_production.py \
  app/services/v2_support_authority.py \
  app/workers/production_workflow.py \
  alembic/versions/0084_youtube_private_delivery.py \
  tests/test_youtube_private_delivery.py
python -m compileall -q app alembic/versions/0084_youtube_private_delivery.py tests/test_youtube_private_delivery.py
```

Required result: one head named `0084_youtube_private_delivery`; all tests, lint, and compile checks pass. Do not bypass a failing check.

## 4. Reconstruct the exact local candidate

Read the runtime database and identify exactly one candidate eligible for this canary. It must have:

- `FinalReviewCandidate` at the current, non-superseded authority;
- one immutable `FinalVideoDecision` with `decision = UPLOAD`;
- `LONG_FORM` production lane;
- checksum-verified `FinalMediaRef` and local archive bytes;
- a verified AI-generated production thumbnail binding;
- a checksum-verified SRT sidecar and subtitle-QC lineage;
- frozen title, description, tags, category, default language, made-for-kids state, synthetic-media disclosure state, channel ID, and destination account identity;
- no existing conflicting private-stage/publication/upload authority;
- no unresolved provider-effect ambiguity for the same identity.

Print only IDs, states, safe refs, and SHA-256 hashes. Never print secret refs that contain provider session material.

If zero or multiple eligible authorities exist, stop and report the ambiguity. Do not choose by recency alone.

## 5. Provision and verify YouTube OAuth authority

Use the operator's existing secure OAuth procedure. Required scopes must include:

- `https://www.googleapis.com/auth/youtube.upload`
- one of `https://www.googleapis.com/auth/youtube.force-ssl` or `https://www.googleapis.com/auth/youtube`

Verify through official readback that the authenticated account owns/manages the exact frozen platform channel. Register one `YouTubePublishingCredential` bound to the exact company/channel/destination. The credential must remain:

```text
public_release_allowed = false
delete_allowed = false
state = ACTIVE
```

Confirm its content hash matches the recomputed identity. Never paste the token into SQL, source files, command history, or the report.

## 6. Dry-run the private stage with provider effects disabled

Keep provider effects disabled. Prepare the private stage from current authority and inspect:

- stage ID and identity hash;
- staging metadata hash;
- public release expectation hash;
- final-media checksum;
- thumbnail binding hash;
- caption checksum;
- credential hash;
- generated outbox command identity.

Recompute all hashes independently using repository helpers. Confirm the stage can only request `privacyStatus=private`, has no `publishAt`, and contains no public/delete authority.

Stop on any drift.

## 7. Execute exactly one live private-upload canary

Obtain explicit operator approval immediately before enabling provider calls. Enable only the private-delivery worker/effect path required for this one stage.

Execute the stage once. Observe and persist:

1. one `YouTubeUploadAttempt`;
2. one sealed provider effect key/request hash;
3. one resumable session secret outside the DB;
4. authenticated session query and byte upload;
5. one remote platform video ID;
6. one thumbnail component attempt/receipt;
7. one caption component attempt/receipt;
8. metadata readback receipt;
9. processing readback receipt;
10. final `PRIVATE_VERIFIED` stage state.

The remote readback must exactly match frozen channel ID, title, description, tags, category, default language, made-for-kids state, synthetic-media state, thumbnail, caption, privacy `PRIVATE`, and processing `SUCCEEDED`.

On timeout/transport uncertainty, stop and reconcile the same persisted session. Never create a replacement upload session.

## 8. Verify operator delivery and local purge

For Telegram, run only when a company-scoped or exact channel-scoped credential and chat binding are configured. Confirm:

- no unscoped/global credential is selected;
- ambiguous credentials fail closed;
- one notification attempt is persisted before the provider call;
- an uncertain Telegram outcome is not blindly repeated.

For local purge, first prove the remote private stage is `PRIVATE_VERIFIED`. Then execute the prepared purge attempt and verify the crash-safe sequence:

```text
INTENDED → SUBMITTED → QUARANTINED → PURGED
```

Evidence required:

- original and quarantine paths remain under `VCOS_V2_PRODUCTION_ROOT`;
- checksum matches before quarantine/unlink;
- active local MP4 is absent afterward;
- `LocalMediaPurgeReceipt` exists with the exact checksum;
- `CloudMediaRef.local_cleanup_status = CLEANED` when the local-archive authority exists;
- caption/thumbnail authorities required for review remain available according to policy.

Do not purge on any weaker remote state.

## 9. Stop at the human boundary

After private verification, stop automation. Report the Studio URL to the human operator without opening or clicking it automatically.

The human must manually inspect the video, thumbnail, captions, disclosures, audience setting, title, description, tags, category, language, and processing state, then manually change visibility to public in YouTube Studio.

Codex must not perform that visibility change.

## 10. Verify canonical publication after the human action

Only after the operator states that the video is public, read the official remote state again and submit `ManualPublishVerificationV2` with the complete observation:

- platform/channel/account identity;
- platform video ID and URL;
- exact title and description;
- exact tags;
- category ID;
- default language;
- privacy `PUBLIC`;
- published timestamp;
- duration;
- made-for-kids state;
- synthetic-media state;
- thumbnail confirmed;
- caption confirmed;
- immutable evidence reference.

Confirm all of the following occur atomically only after exact public readback:

- `ManualPublishConfirmation = VERIFIED`;
- one immutable `PublicPublicationReceipt`;
- one canonical `UploadedVideo` schema v3;
- `analytics_sync_status = READY`;
- long-form analytics windows are scheduled;
- series publication advances only for a valid series episode;
- a YouTube series episode can receive a public ordinal only after its publication receipt exists.

Prove that none of these existed while the video was private.

## 11. Required final report

Return one redacted Markdown report with these sections:

```text
PR3_LOCAL_CLOSEOUT_STATUS
REPOSITORY_AUTHORITY
INITIAL_DIRTY_TREE_PRESERVED
MIGRATION_AND_TEST_RESULTS
CANDIDATE_AND_HASH_LINEAGE
OAUTH_SCOPE_AND_CHANNEL_BINDING
PRIVATE_STAGE_EXECUTION
REMOTE_PRIVATE_READBACK
TELEGRAM_RESULT
LOCAL_PURGE_RESULT
HUMAN_PUBLIC_RELEASE_BOUNDARY
PUBLICATION_VERIFICATION
ANALYTICS_AND_SERIES_ACTIVATION
OPEN_INCIDENTS_OR_AMBIGUITIES
FINAL_VERDICT
```

Use explicit booleans:

```text
SECOND_VIDEO_INSERT_EMITTED=false
PUBLIC_RELEASE_API_CALLED=false
DELETE_API_CALLED=false
MAIN_MUTATED=false
SECRETS_EXPOSED=false
PRIVATE_STAGE_VERIFIED=true|false
PUBLICATION_RECEIPT_CREATED=true|false
CANONICAL_UPLOADED_VIDEO_CREATED=true|false
LOCAL_PURGE_VERIFIED=true|false
SAFE_TO_OPERATE=true|false
```

A successful verdict requires every relevant boolean and every database/provider/filesystem proof. Otherwise return `SAFE_TO_OPERATE=false` with the exact blocker and next deterministic action.
'''
write(
    "docs/operations/youtube_private_delivery_local_codex_master_prompt.md",
    master_prompt.rstrip() + "\n",
)

print("PR #3 closeout patch applied successfully")

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.ops import ProviderRegistryEntryCreate
from app.db.models import User
from app.services import (
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    GateDefinitionService,
    ProviderRegistryService,
    RBACService,
)
from app.services.ofv0 import FormatIdentityContractService


ROOT = Path(__file__).resolve().parents[2]


class QualificationFactory:
    """Generic production-like channel/profile fixture for regression tests.

    The factory intentionally creates no named channel, niche-specific policy
    overlay, or per-channel execution path. Both normal and strict long-form
    fixtures are compiled through the same ChannelProfileCompiler.
    """

    def __init__(self, session):
        self.session = session

    def seed_all(self) -> None:
        ConfigRegistryService(self.session).seed([ROOT / "config"])
        registry = ProviderRegistryService(self.session)
        if registry.get_entry("openai") is None:
            registry.create_entry(
                data=ProviderRegistryEntryCreate(
                    provider_key="openai",
                    provider_name="OpenAI Responses Router",
                    provider_type="LLM",
                    capability_blob={
                        "llm_router_lane_bound": True,
                        "guarded_real_execution": True,
                    },
                    policy_fit_blob={"production_enabled_when_configured": True},
                    metadata={"readiness_provider_key": "openai"},
                )
            )
        GateDefinitionService(self.session).seed_definitions()

    def user(
        self,
        *,
        role_key: str = "operator",
        company_id=None,
        email_prefix: str = "qual",
    ) -> User:
        user = User(
            email=f"{email_prefix}-{uuid.uuid4().hex[:10]}@example.com",
            display_name=email_prefix,
            status="active",
        )
        self.session.add(user)
        self.session.flush()
        if company_id is not None:
            RBACService(self.session).assign_role(
                user_id=user.id,
                role_key=role_key,
                company_id=company_id,
            )
        return user

    def channel_scope(
        self,
        *,
        name: str = "Qualification",
        strict_long_form: bool = False,
    ) -> SimpleNamespace:
        self.seed_all()
        company = CompanyService(self.session).create_company(name=f"{name} Co")
        operator = self.user(
            role_key="operator",
            company_id=company.id,
            email_prefix="operator",
        )
        admin = self.user(
            role_key="company_admin",
            company_id=company.id,
            email_prefix="admin",
        )
        channel = ChannelWorkspaceService(self.session).create_channel(
            company_id=company.id,
            data=ChannelWorkspaceCreate(
                key=f"qualification-{uuid.uuid4().hex[:12]}",
                name=f"{name} Channel",
            ),
        )
        profile = ChannelProfileService(self.session).create_profile_version(
            channel_id=channel.id,
            data=ChannelProfileVersionCreate(
                template_key="saas_digital_leverage",
                created_by=admin.id,
            ),
        )
        if strict_long_form:
            format_contract = FormatIdentityContractService(self.session).draft(
                FormatIdentityContractDraftRequest(
                    channel_id=channel.id,
                    channel_profile_version_id=profile.id,
                    created_by="QualificationFactory",
                )
            )
            FormatIdentityContractService(self.session).approve(
                format_contract.id,
                decided_by="qualification-operator",
            )
        compiled = ChannelProfileCompiler(self.session).compile(
            profile_version_id=profile.id,
            correlation_id=f"qualification-compile-{uuid.uuid4().hex[:8]}",
        )
        profiles = ChannelProfileService(self.session)
        profiles.submit_for_approval(profile.id)
        profiles.approve_profile_version(
            profile_version_id=profile.id,
            approved_by=admin.id,
            approval_ref="operator-approval://qualification/generic-profile",
        )
        snapshot = profiles.activate_snapshot(snapshot_id=compiled.snapshot_id)
        profile = profiles.get_profile_version(profile.id)
        return SimpleNamespace(
            company=company,
            channel=channel,
            profile=profile,
            snapshot=snapshot,
            operator=operator,
            admin=admin,
            compiled=compiled,
        )

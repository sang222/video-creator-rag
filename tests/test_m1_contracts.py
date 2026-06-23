from app.contracts import (
    CapabilityMatrix,
    ChannelProfileInput,
    CompiledChannelPolicyPayload,
    NicheProfileTemplate,
)
from app.services.profile_compiler import ChannelProfileCompiler


def test_channel_profile_input_validates(db_session) -> None:
    profile_input, _ = ChannelProfileCompiler(db_session).profile_input_from_template("saas_digital_leverage")
    assert ChannelProfileInput.model_validate(profile_input.model_dump()).template_key == "saas_digital_leverage"


def test_niche_profile_template_validates(db_session) -> None:
    _, catalogs = ChannelProfileCompiler(db_session).profile_input_from_template("saas_digital_leverage")
    assert NicheProfileTemplate.model_validate(catalogs.template.model_dump()).template_key == "saas_digital_leverage"


def test_capability_matrix_validates(db_session) -> None:
    _, catalogs = ChannelProfileCompiler(db_session).profile_input_from_template("saas_digital_leverage")
    assert CapabilityMatrix.model_validate(catalogs.capability_matrix.model_dump()).policy_snapshot_available


def test_compiled_channel_policy_snapshot_contract_validates(db_session) -> None:
    compiler = ChannelProfileCompiler(db_session)
    profile_input, catalogs = compiler.profile_input_from_template("saas_digital_leverage")
    payload, _ = compiler.compile_from_input(
        profile_input=profile_input,
        template=catalogs.template,
        capability_matrix=catalogs.capability_matrix,
        compiler_policy=catalogs.compiler_policy,
    )
    assert CompiledChannelPolicyPayload.model_validate(payload).render_policy.production_renderer_planned == "ffmpeg"

"""Deterministic Card E compilers for semantic profile authority."""

from __future__ import annotations

from app.contracts.semantic import (
    ChannelSemanticProfile,
    FormatSemanticProfile,
    SemanticKernelDefinition,
    SemanticProfileCompilation,
)


class SemanticProfileCompiler:
    """Compile channel identity and format grammar without coupling either one.

    The compiler has no database side effects.  Callers persist the resulting
    sealed payload through the existing immutable ArtifactVersion mechanism.
    """

    version = "semantic-profile-compiler.v1"

    @classmethod
    def compile(
        cls,
        *,
        kernel: SemanticKernelDefinition,
        channel_profile: ChannelSemanticProfile,
        format_profile: FormatSemanticProfile,
    ) -> SemanticProfileCompilation:
        kernel.verify_integrity()
        channel_profile.verify_integrity()
        format_profile.verify_integrity()
        return SemanticProfileCompilation.build(
            kernel=kernel,
            channel_profile=channel_profile,
            format_profile=format_profile,
        )

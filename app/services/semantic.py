"""Deterministic Card E compilers for semantic profile authority."""

from __future__ import annotations

from app.contracts.semantic import (
    ChannelSemanticProfile,
    FormatSemanticProfile,
    SemanticKernelDefinition,
    SemanticProfileCompilation,
)


class SemanticProfileCompiler:
    """Pure Card E subcompiler for channel and format semantic authority.

    This is not a second top-level channel-profile authority and has no
    database side effects.  A later owning integration must make the canonical
    ``ChannelProfileCompiler`` consume or delegate to this subcompiler.  The
    resulting payload is compatible with the artifact system, but Card E does
    not itself persist semantic snapshots through ``ArtifactVersion``.
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

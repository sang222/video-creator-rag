"""Repository-backed, deterministic capability projections.

The package intentionally contains procedures and their compiler, not a
runtime agent registry.  Provider calls receive a compact projection only.
"""

from app.capabilities.compiler import (
    CapabilityCompilationError,
    CapabilityCompiler,
    CompiledSkillProjection,
    InstructionPrimitive,
    SkillDefinition,
    default_capability_compiler,
)

__all__ = [
    "CapabilityCompilationError",
    "CapabilityCompiler",
    "CompiledSkillProjection",
    "InstructionPrimitive",
    "SkillDefinition",
    "default_capability_compiler",
]

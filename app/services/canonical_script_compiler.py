"""Deterministic V2 canonical-script compilation.

Provider narration lives only in ``sections[].narration``.  This module is a
small, deliberately non-creative compiler: it normalizes storage-safe text,
orders sections by their explicit ordinal, and joins them with a fixed
versioned separator.  It never repairs, paraphrases, de-duplicates, or drops
content.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.contracts.script_qualification import QualifiedScriptOutputV2
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.services.config_registry import content_hash


SCRIPT_CONTRACT_V2 = "V2_SINGLE_SOURCE"
CANONICAL_SCRIPT_COMPILER_VERSION = "canonical-script-compiler.v2"
CANONICAL_SCRIPT_SEPARATOR = "\n\n"
CANONICAL_SCRIPT_NORMALIZATION_POLICY = "unicode-nfc-line-endings-trim.v1"


def normalize_section_narration(value: str) -> str:
    """Apply only the V2 contract's permitted storage normalization."""

    return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n").strip()


def section_narration_hash(value: str) -> str:
    return hashlib.sha256(normalize_section_narration(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CompiledCanonicalScript:
    script_contract_version: str
    compiler_version: str
    ordered_section_ids: list[str]
    ordered_section_hashes: list[str]
    separator_policy: str
    normalization_policy: str
    canonical_script: str
    canonical_script_hash: str
    section_set_hash: str
    total_word_count: int
    estimated_duration_ms: int
    compiled_at: datetime

    def payload(self) -> dict[str, Any]:
        return {
            "script_contract_version": self.script_contract_version,
            "compiler_version": self.compiler_version,
            "ordered_section_ids": self.ordered_section_ids,
            "ordered_section_hashes": self.ordered_section_hashes,
            "separator_policy": self.separator_policy,
            "normalization_policy": self.normalization_policy,
            "canonical_script": self.canonical_script,
            "canonical_script_hash": self.canonical_script_hash,
            "section_set_hash": self.section_set_hash,
            "total_word_count": self.total_word_count,
            "estimated_duration_ms": self.estimated_duration_ms,
            "compiled_at": self.compiled_at.isoformat(),
        }


class CanonicalScriptCompiler:
    """Pure compiler for the single-source V2 narration contract."""

    @classmethod
    def compile(
        cls,
        output: QualifiedScriptOutputV2,
        *,
        duration_estimation_wpm: int = 150,
        compiled_at: datetime | None = None,
    ) -> CompiledCanonicalScript:
        if duration_estimation_wpm <= 0:
            raise ValidationFailureError("CANONICAL_SCRIPT_COMPILER_WPM_INVALID")
        sections = list(output.sections)
        ids = [item.section_id.strip() for item in sections]
        ordinals = [item.ordinal for item in sections]
        narrations = [normalize_section_narration(item.narration) for item in sections]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            raise ValidationFailureError("SCRIPT_STRUCTURE_SECTION_IDS_INVALID")
        if len(set(ordinals)) != len(ordinals):
            raise ValidationFailureError("SCRIPT_STRUCTURE_DUPLICATE_ORDINAL")
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValidationFailureError("SCRIPT_STRUCTURE_ORDINALS_NOT_CONTIGUOUS")
        if any(not item for item in narrations):
            raise ValidationFailureError("SCRIPT_STRUCTURE_EMPTY_NARRATION")

        ordered = sorted(
            zip(sections, narrations, strict=True), key=lambda item: item[0].ordinal
        )
        ordered_ids = [item.section_id.strip() for item, _ in ordered]
        ordered_hashes = [section_narration_hash(narration) for _, narration in ordered]
        canonical_script = CANONICAL_SCRIPT_SEPARATOR.join(
            narration for _, narration in ordered
        )
        if not canonical_script:
            raise ValidationFailureError("SCRIPT_STRUCTURE_EMPTY_NARRATION")
        canonical_hash = hashlib.sha256(canonical_script.encode("utf-8")).hexdigest()
        section_set_hash = content_hash(
            {
                "script_contract_version": SCRIPT_CONTRACT_V2,
                "compiler_version": CANONICAL_SCRIPT_COMPILER_VERSION,
                "ordered_section_ids": ordered_ids,
                "ordered_section_hashes": ordered_hashes,
                "separator_policy": CANONICAL_SCRIPT_SEPARATOR,
                "normalization_policy": CANONICAL_SCRIPT_NORMALIZATION_POLICY,
            }
        )
        words = re.findall(r"\b[\w'-]+\b", canonical_script, flags=re.UNICODE)
        return CompiledCanonicalScript(
            script_contract_version=SCRIPT_CONTRACT_V2,
            compiler_version=CANONICAL_SCRIPT_COMPILER_VERSION,
            ordered_section_ids=ordered_ids,
            ordered_section_hashes=ordered_hashes,
            separator_policy=CANONICAL_SCRIPT_SEPARATOR,
            normalization_policy=CANONICAL_SCRIPT_NORMALIZATION_POLICY,
            canonical_script=canonical_script,
            canonical_script_hash=canonical_hash,
            section_set_hash=section_set_hash,
            total_word_count=len(words),
            estimated_duration_ms=round(len(words) / duration_estimation_wpm * 60_000),
            compiled_at=compiled_at or utc_now(),
        )

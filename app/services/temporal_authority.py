from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.temporal_authority import (
    AlignedWord,
    CanonicalMediaTimeline,
    CanonicalTimelineSegment,
    CharacterAlignment,
    EditorialScriptText,
    EditorialSegmentInput,
    FinalNarrationAudio,
    ForcedAlignmentEvidence,
    NarrationTimingSeed,
    NormalizationOperation,
    PhraseBoundary,
    SourceToSpokenSpan,
    SpokenTextNormalized,
    SpokenToken,
    TemporalAuthorityGateResult,
    TextSpan,
    TimingConflict,
    VerifiedNarrationAlignment,
    VerifiedNarrationWord,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.config import Settings, get_settings
from app.db.models import Artifact, ArtifactVersion, VideoProject
from app.services.native_render_plan import stable_hash
from app.services.workflow import ArtifactService


NORMALIZATION_VERSION = "spoken-text-normalizer/en-v1.0.0"
TIMELINE_VERSION = "canonical-media-timeline/v1.0.0"
DEFAULT_ABBREVIATIONS = {
    "Dr.": "Doctor",
    "Mr.": "Mister",
    "Mrs.": "Missus",
    "Ms.": "Miss",
    "e.g.": "for example",
    "i.e.": "that is",
    "vs.": "versus",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_URL_RE = re.compile(r"(?:https?://|www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z]{2,})(?:/[^\s]*)?")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY_RE = re.compile(r"\$\d[\d,]*(?:\.\d{1,2})?")
_PERCENT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%")
_RANGE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?[-–]\d[\d,]*(?:\.\d+)?")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ACRONYM_RE = re.compile(r"[A-Z]{2,8}\b")
_WHITESPACE_RE = re.compile(r"\s+")


def _sha_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _comparison_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_ORDINAL_DAYS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh",
    8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth",
    14: "fourteenth", 15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
    19: "nineteenth", 20: "twentieth", 21: "twenty first", 22: "twenty second", 23: "twenty third",
    24: "twenty fourth", 25: "twenty fifth", 26: "twenty sixth", 27: "twenty seventh",
    28: "twenty eighth", 29: "twenty ninth", 30: "thirtieth", 31: "thirty first",
}


def _integer_words(number: int) -> str:
    if number < 0:
        return "minus " + _integer_words(-number)
    if number < 20:
        return _ONES[number]
    if number < 100:
        tens, rest = divmod(number, 10)
        return _TENS[tens] + (" " + _integer_words(rest) if rest else "")
    if number < 1_000:
        hundreds, rest = divmod(number, 100)
        return _ONES[hundreds] + " hundred" + (" " + _integer_words(rest) if rest else "")
    for unit, label in ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")):
        if number >= unit:
            head, rest = divmod(number, unit)
            return _integer_words(head) + " " + label + (" " + _integer_words(rest) if rest else "")
    raise ValueError("NUMBER_OUT_OF_SUPPORTED_RANGE")


def _number_words(value: str) -> str:
    clean = value.replace(",", "")
    if "." not in clean:
        return _integer_words(int(clean))
    whole, decimal = clean.split(".", 1)
    return f"{_integer_words(int(whole))} point {' '.join(_ONES[int(item)] for item in decimal)}"


def _date_words(value: str) -> str:
    year_text, month_text, day_text = value.split("-")
    year, month, day = int(year_text), int(month_text), int(day_text)
    if month not in range(1, 13) or day not in _ORDINAL_DAYS:
        raise ValueError("AMBIGUOUS_DATE_NORMALIZATION")
    return f"{_MONTHS[month]} {_ORDINAL_DAYS[day]} {_integer_words(year)}"


def _currency_words(value: str) -> str:
    amount = value[1:].replace(",", "")
    whole_text, dot, cents_text = amount.partition(".")
    whole = int(whole_text)
    result = f"{_integer_words(whole)} {'dollar' if whole == 1 else 'dollars'}"
    if dot:
        cents = int(cents_text.ljust(2, "0"))
        if cents:
            result += f" and {_integer_words(cents)} {'cent' if cents == 1 else 'cents'}"
    return result


def _url_words(value: str) -> str:
    output = value
    for prefix in ("https://", "http://"):
        if output.startswith(prefix):
            output = output[len(prefix):]
    output = output.replace("www.", "w w w dot ", 1)
    replacements = {
        ".": " dot ", "/": " slash ", "-": " dash ", "_": " underscore ",
        ":": " colon ", "?": " question mark ", "=": " equals ", "&": " and ",
    }
    for source, spoken in replacements.items():
        output = output.replace(source, spoken)
    return " ".join(output.split())


@dataclass(frozen=True)
class _Chunk:
    source_start: int
    source_end: int
    source_text: str
    spoken_text: str
    operation_type: str | None
    reason_code: str | None


class SpokenTextNormalizer:
    """Deterministic English normalizer with complete source/spoken traceability."""

    def __init__(self, *, normalization_version: str = NORMALIZATION_VERSION):
        self.normalization_version = normalization_version

    def normalize(
        self,
        *,
        script_revision_id: str,
        source_text: str,
        locale: str = "en-US",
        language: str = "en",
        channel_pronunciation_policy: dict[str, Any] | None = None,
        pronunciation_dictionary: dict[str, str] | None = None,
        pronunciation_dictionary_refs: list[str] | None = None,
        abbreviation_policy: dict[str, str] | None = None,
        normalization_policy_version: str | None = None,
    ) -> SpokenTextNormalized:
        if not script_revision_id or not source_text:
            raise ValueError("EDITORIAL_SCRIPT_REQUIRED")
        if language.casefold() != "en" or not locale.casefold().startswith("en"):
            raise ValueError("NORMALIZATION_LOCALE_NOT_SUPPORTED")
        channel_policy = dict(channel_pronunciation_policy or {})
        unknown_policy_keys = set(channel_policy) - {"approved", "abbreviations", "pronunciations", "policy_ref"}
        if unknown_policy_keys or channel_policy.get("approved") is False:
            raise ValueError("CHANNEL_PRONUNCIATION_POLICY_INVALID")
        dictionary = {
            **dict(channel_policy.get("pronunciations") or {}),
            **dict(pronunciation_dictionary or {}),
        }
        if any(not key for key in dictionary):
            raise ValueError("PRONUNCIATION_DICTIONARY_KEY_INVALID")
        if any(not str(value).strip() for value in dictionary.values()):
            raise ValueError("SEMANTIC_TEXT_DELETION_BLOCKED")
        abbreviations = {
            **DEFAULT_ABBREVIATIONS,
            **dict(channel_policy.get("abbreviations") or {}),
            **(abbreviation_policy or {}),
        }
        if any(not str(value).strip() for value in abbreviations.values()):
            raise ValueError("SEMANTIC_TEXT_DELETION_BLOCKED")
        for match in re.finditer(r"\b([A-Za-z]{2,10})\.(?=\s+\d)", source_text):
            if match.group(0) not in abbreviations:
                raise ValueError("AMBIGUOUS_NORMALIZATION")
        if any(symbol in source_text for symbol in ("~", "^")):
            raise ValueError("AMBIGUOUS_NORMALIZATION")

        chunks: list[_Chunk] = []
        position = 0
        while position < len(source_text):
            match, operation_type, reason_code, replacement = self._match_transform(
                source_text,
                position,
                dictionary=dictionary,
                abbreviations=abbreviations,
            )
            if match is None:
                end = position + 1
                chunks.append(_Chunk(position, end, source_text[position:end], source_text[position:end], None, None))
                position = end
                continue
            source_value = match.group(0)
            spoken_value = replacement(source_value) if callable(replacement) else replacement
            if spoken_value is None:
                spoken_value = source_value
            if operation_type == "WHITESPACE_NORMALIZATION":
                has_prior = any(chunk.spoken_text for chunk in chunks)
                has_future = bool(source_text[match.end():].strip())
                spoken_value = " " if has_prior and has_future else ""
            chunks.append(
                _Chunk(position, match.end(), source_value, str(spoken_value), operation_type, reason_code)
            )
            position = match.end()

        spoken_parts: list[str] = []
        mappings: list[SourceToSpokenSpan] = []
        operations: list[NormalizationOperation] = []
        operation_by_id: dict[str, NormalizationOperation] = {}
        spoken_cursor = 0
        source_cursor = 0
        for index, chunk in enumerate(chunks, start=1):
            if chunk.source_start != source_cursor or chunk.source_end <= chunk.source_start:
                raise ValueError("NORMALIZATION_SOURCE_ACCOUNTING_GAP")
            start = spoken_cursor
            spoken_parts.append(chunk.spoken_text)
            spoken_cursor += len(chunk.spoken_text)
            operation_ids: list[str] = []
            if chunk.operation_type and chunk.source_text != chunk.spoken_text:
                operation_id = f"norm-{index:04d}"
                operation = NormalizationOperation(
                    operation_id=operation_id,
                    operation_type=chunk.operation_type,
                    source_span=TextSpan(start=chunk.source_start, end=chunk.source_end),
                    spoken_span=TextSpan(start=start, end=spoken_cursor),
                    source_text=chunk.source_text,
                    spoken_text=chunk.spoken_text,
                    reason_code=chunk.reason_code or "NORMALIZATION_RULE_APPLIED",
                    whitelisted=True,
                )
                operations.append(operation)
                operation_by_id[operation_id] = operation
                operation_ids.append(operation_id)
            mappings.append(
                SourceToSpokenSpan(
                    source_span=TextSpan(start=chunk.source_start, end=chunk.source_end),
                    spoken_span=TextSpan(start=start, end=spoken_cursor),
                    operation_ids=operation_ids,
                )
            )
            source_cursor = chunk.source_end
        if source_cursor != len(source_text):
            raise ValueError("NORMALIZATION_SOURCE_ACCOUNTING_GAP")

        spoken_text = "".join(spoken_parts)
        if not spoken_text.strip():
            raise ValueError("SEMANTIC_TEXT_DELETION_BLOCKED")
        tokens: list[SpokenToken] = []
        for index, match in enumerate(_TOKEN_RE.finditer(spoken_text), start=1):
            matching = [
                item for item in mappings
                if item.spoken_span.start < match.end() and item.spoken_span.end > match.start()
            ]
            source_spans = [item.source_span for item in matching]
            if not source_spans:
                raise ValueError("NORMALIZATION_UNMAPPED_INSERTED_WORD")
            operation_ids = sorted({operation_id for item in matching for operation_id in item.operation_ids})
            comparison = _comparison_key(match.group(0))
            if not comparison:
                raise ValueError("NORMALIZATION_TOKEN_INVALID")
            tokens.append(
                SpokenToken(
                    token_id=f"spoken-{index:04d}",
                    text=match.group(0),
                    spoken_span=TextSpan(start=match.start(), end=match.end()),
                    source_spans=source_spans,
                    normalization_operation_ids=operation_ids,
                    comparison_key=comparison,
                )
            )
        if not tokens:
            raise ValueError("NORMALIZATION_SPOKEN_TOKENS_MISSING")
        payload = {
            "normalization_version": normalization_policy_version or self.normalization_version,
            "script_revision_id": script_revision_id,
            "source_text_hash": _sha_text(source_text),
            "source_character_count": len(source_text),
            "spoken_text": spoken_text,
            "spoken_text_hash": _sha_text(spoken_text),
            "spoken_character_count": len(spoken_text),
            "normalization_operations": [item.model_dump(mode="json") for item in operations],
            "source_to_spoken_spans": [item.model_dump(mode="json") for item in mappings],
            "spoken_tokens": [item.model_dump(mode="json") for item in tokens],
            "pronunciation_dictionary_refs": sorted(pronunciation_dictionary_refs or []),
            "normalization_warnings": [],
        }
        return SpokenTextNormalized(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def editorial_script(*, script_revision_id: str, text: str, locale: str = "en-US", language: str = "en") -> EditorialScriptText:
        payload = {"script_revision_id": script_revision_id, "text": text, "locale": locale, "language": language}
        return EditorialScriptText(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def _match_transform(
        text: str,
        position: int,
        *,
        dictionary: dict[str, str],
        abbreviations: dict[str, str],
    ) -> tuple[re.Match[str] | None, str | None, str | None, Any]:
        for source in sorted(dictionary, key=lambda item: (-len(item), item)):
            if text.startswith(source, position):
                before_ok = position == 0 or not text[position - 1].isalnum()
                after = position + len(source)
                after_ok = after == len(text) or not text[after].isalnum()
                if before_ok and after_ok:
                    return re.compile(re.escape(source)).match(text, position), "PRONUNCIATION_DICTIONARY_MAPPING", "APPROVED_PRONUNCIATION_DICTIONARY", dictionary[source]
        for source in sorted(abbreviations, key=lambda item: (-len(item), item)):
            if text.startswith(source, position):
                return re.compile(re.escape(source)).match(text, position), "ABBREVIATION_EXPANSION", "KNOWN_ABBREVIATION", abbreviations[source]
        rules: tuple[tuple[re.Pattern[str], str, str, Any], ...] = (
            (_URL_RE, "URL_PRONUNCIATION", "APPROVED_URL_RULE", _url_words),
            (_ISO_DATE_RE, "DATE_VERBALIZATION", "ISO_DATE", _date_words),
            (_CURRENCY_RE, "CURRENCY_VERBALIZATION", "USD_CURRENCY", _currency_words),
            (_PERCENT_RE, "PERCENTAGE_VERBALIZATION", "PERCENT_SYMBOL", lambda value: _number_words(value[:-1]) + " percent"),
            (_RANGE_RE, "NUMBER_RANGE_VERBALIZATION", "NUMBER_RANGE", lambda value: " to ".join(_number_words(part) for part in re.split(r"[-–]", value))),
            (_NUMBER_RE, "NUMBER_VERBALIZATION", "CARDINAL_OR_DECIMAL_NUMBER", _number_words),
            (_ACRONYM_RE, "ACRONYM_PRONUNCIATION", "KNOWN_ACRONYM_PATTERN", lambda value: " ".join(value)),
            (_WHITESPACE_RE, "WHITESPACE_NORMALIZATION", "WHITESPACE_CANONICALIZATION", " "),
        )
        for pattern, operation_type, reason_code, replacement in rules:
            match = pattern.match(text, position)
            if match:
                return match, operation_type, reason_code, replacement
        return None, None, None, None


class ElevenLabsTimestampRequestBuilder:
    """Builds the repaired TTS contract; it has no transport or credential input."""

    def build(
        self,
        *,
        normalized: SpokenTextNormalized,
        voice_id: str,
        model_id: str,
        voice_settings: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        dictionary_locators = []
        for ref in normalized.pronunciation_dictionary_refs:
            dictionary_id, separator, version_id = ref.partition(":")
            if not separator or not dictionary_id or not version_id:
                raise ValueError("PRONUNCIATION_DICTIONARY_REF_VERSION_MISSING")
            dictionary_locators.append(
                {"pronunciation_dictionary_id": dictionary_id, "version_id": version_id}
            )
        payload = {
            "text": normalized.spoken_text,
            "model_id": model_id,
            "voice_settings": dict(voice_settings or {}),
            "pronunciation_dictionary_locators": dictionary_locators,
            "apply_text_normalization": "off",
        }
        if seed is not None:
            payload["seed"] = seed
        request = {
            "provider_key": "elevenlabs",
            "endpoint_path": f"/v1/text-to-speech/{voice_id}/with-timestamps",
            "endpoint_semantics": "CONVERT_WITH_TIMESTAMPS",
            "voice_id": voice_id,
            "source_text_hash": normalized.source_text_hash,
            "spoken_text_hash": normalized.spoken_text_hash,
            "payload": payload,
            "transport_enabled": False,
            "provider_call_made": False,
        }
        return {**request, "request_hash": stable_hash(request)}


class ElevenLabsTimingResponseParser:
    def parse(
        self,
        *,
        response: dict[str, Any],
        normalized: SpokenTextNormalized,
        audio_asset_ref: str,
        audio_duration_ms: int,
        model_id: str,
        voice_id: str,
        voice_settings: dict[str, Any] | None = None,
        pronunciation_dictionary_refs: list[str] | None = None,
        seed: int | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> NarrationTimingSeed:
        if audio_duration_ms <= 0:
            raise ValueError("TEMPORAL_AUDIO_DURATION_INVALID")
        warnings: list[str] = []
        original = self._characters(response.get("alignment"), audio_duration_ms, "PROVIDER_ALIGNMENT")
        normalized_chars = self._characters(response.get("normalized_alignment"), audio_duration_ms, "PROVIDER_NORMALIZED_ALIGNMENT")
        normalized_chars, boundary_whitespace_trimmed = self._trim_boundary_whitespace(
            normalized_chars,
            expected_text=normalized.spoken_text,
        )
        if boundary_whitespace_trimmed:
            warnings.append("WHITELISTED_PROVIDER_BOUNDARY_WHITESPACE")
        reconstructed = "".join(item.character for item in sorted(normalized_chars, key=lambda item: item.character_index))
        if normalized_chars and reconstructed != normalized.spoken_text:
            warnings.append("NORMALIZED_ALIGNMENT_TEXT_MISMATCH")
        if not normalized_chars:
            warnings.append("PROVIDER_NORMALIZED_TIMING_MISSING")
        fatal_warnings = {
            "NORMALIZED_ALIGNMENT_TEXT_MISMATCH",
            "PROVIDER_NORMALIZED_TIMING_MISSING",
        }
        timing_available = bool(normalized_chars) and not any(
            warning in fatal_warnings for warning in warnings
        )
        headers = {key.casefold(): value for key, value in (response_headers or {}).items()}
        provider_request_id = str(response.get("request_id") or headers.get("request-id") or headers.get("x-request-id") or "") or None
        response_metadata = {
            "audio_payload_present": bool(response.get("audio_base64") or response.get("audio")),
            "alignment_present": bool(response.get("alignment")),
            "normalized_alignment_present": bool(response.get("normalized_alignment")),
            "normalized_alignment_boundary_whitespace_trimmed": boundary_whitespace_trimmed,
        }
        payload = {
            "provider_key": "elevenlabs",
            "provider_request_id": provider_request_id,
            "audio_asset_ref": audio_asset_ref,
            "audio_duration_ms": audio_duration_ms,
            "source_text_hash": normalized.source_text_hash,
            "spoken_text_hash": normalized.spoken_text_hash,
            "original_character_alignment": [item.model_dump(mode="json") for item in original],
            "normalized_character_alignment": [item.model_dump(mode="json") for item in normalized_chars],
            "provider_model_id": model_id,
            "provider_voice_id": voice_id,
            "seed": seed,
            "voice_settings": dict(voice_settings or {}),
            "pronunciation_dictionary_refs": sorted(pronunciation_dictionary_refs or []),
            "response_metadata": response_metadata,
            "timing_available": timing_available,
            "timing_parse_warnings": warnings,
        }
        return NarrationTimingSeed(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def _trim_boundary_whitespace(
        characters: list[CharacterAlignment],
        *,
        expected_text: str,
    ) -> tuple[list[CharacterAlignment], bool]:
        if not characters or expected_text != expected_text.strip():
            return characters, False
        reconstructed = "".join(item.character for item in characters)
        if reconstructed == expected_text or reconstructed.strip() != expected_text:
            return characters, False
        leading = len(reconstructed) - len(reconstructed.lstrip())
        trailing = len(reconstructed) - len(reconstructed.rstrip())
        end = len(characters) - trailing if trailing else len(characters)
        retained = characters[leading:end]
        if "".join(item.character for item in retained) != expected_text:
            return characters, False
        return (
            [
                CharacterAlignment(
                    character_index=index,
                    character=item.character,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                )
                for index, item in enumerate(retained)
            ],
            True,
        )

    @staticmethod
    def _characters(raw: Any, duration_ms: int, label: str) -> list[CharacterAlignment]:
        if not isinstance(raw, dict):
            return []
        characters = raw.get("characters") or []
        starts = raw.get("character_start_times_seconds") or raw.get("character_start_times") or []
        ends = raw.get("character_end_times_seconds") or raw.get("character_end_times") or []
        if not (isinstance(characters, list) and isinstance(starts, list) and isinstance(ends, list)):
            raise ValueError(f"{label}_SHAPE_INVALID")
        if len(characters) != len(starts) or len(characters) != len(ends):
            raise ValueError(f"{label}_LENGTH_MISMATCH")
        result: list[CharacterAlignment] = []
        last_start = -1
        for index, (character, start, end) in enumerate(zip(characters, starts, ends, strict=True)):
            start_ms, end_ms = round(float(start) * 1000), round(float(end) * 1000)
            if start_ms < last_start:
                raise ValueError("TEMPORAL_ALIGNMENT_NON_MONOTONIC")
            if start_ms < 0 or end_ms <= start_ms or end_ms > duration_ms:
                raise ValueError("TEMPORAL_ALIGNMENT_AUDIO_BOUNDS_INVALID")
            result.append(CharacterAlignment(character_index=index, character=str(character), start_ms=start_ms, end_ms=end_ms))
            last_start = start_ms
        return result


class ElevenLabsForcedAlignmentRequestBuilder:
    def build(self, *, audio_asset_ref: str, normalized: SpokenTextNormalized) -> dict[str, Any]:
        request = {
            "provider_key": "elevenlabs",
            "endpoint_path": "/v1/forced-alignment",
            "audio_asset_ref": audio_asset_ref,
            "spoken_text_hash": normalized.spoken_text_hash,
            "text": normalized.spoken_text,
            "multipart_field_names": ["file", "text"],
            "transport_enabled": False,
            "provider_call_made": False,
        }
        return {**request, "request_hash": stable_hash(request)}


class FixtureOnlyAlignmentTransport:
    provider_call_made = False
    network_call_made = False

    def execute(self, *, request: dict[str, Any], fixture_response: dict[str, Any]) -> dict[str, Any]:
        if request.get("transport_enabled") is not False:
            raise ValueError("FIXTURE_TRANSPORT_REQUIRES_DISABLED_REQUEST")
        return json.loads(json.dumps(fixture_response))


class NarrationAlignmentVerifier:
    """Provider-neutral forced-alignment boundary with injected contract adapters."""

    def __init__(
        self,
        *,
        request_builder: ElevenLabsForcedAlignmentRequestBuilder | None = None,
        response_parser: "ElevenLabsForcedAlignmentResponseParser | None" = None,
    ):
        self.request_builder = request_builder or ElevenLabsForcedAlignmentRequestBuilder()
        self.response_parser = response_parser or ElevenLabsForcedAlignmentResponseParser()

    def build_request(self, *, audio_asset_ref: str, normalized: SpokenTextNormalized) -> dict[str, Any]:
        return self.request_builder.build(audio_asset_ref=audio_asset_ref, normalized=normalized)

    def parse_evidence(self, **kwargs: Any) -> ForcedAlignmentEvidence:
        return self.response_parser.parse(**kwargs)


class ElevenLabsForcedAlignmentResponseParser:
    def parse(
        self,
        *,
        response: dict[str, Any],
        normalized: SpokenTextNormalized,
        audio_asset_ref: str,
        audio_duration_ms: int,
        response_headers: dict[str, str] | None = None,
    ) -> ForcedAlignmentEvidence:
        raw_words = [item for item in (response.get("words") or []) if isinstance(item, dict) and item.get("type", "word") == "word"]
        words: list[AlignedWord] = []
        last_start = -1
        skipped_empty_word_count = 0
        for item in raw_words:
            start_ms = self._time_ms(item, "start")
            end_ms = self._time_ms(item, "end")
            if start_ms < last_start:
                raise ValueError("TEMPORAL_ALIGNMENT_NON_MONOTONIC")
            if start_ms < 0 or end_ms <= start_ms or end_ms > audio_duration_ms:
                raise ValueError("TEMPORAL_ALIGNMENT_AUDIO_BOUNDS_INVALID")
            text = str(item.get("text") or item.get("word") or "").strip()
            last_start = start_ms
            if not text:
                skipped_empty_word_count += 1
                continue
            words.append(
                AlignedWord(
                    word_id=f"forced-{len(words) + 1:04d}",
                    text=text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    loss=float(item["loss"]) if item.get("loss") is not None else None,
                    source_spoken_token_ids=[],
                )
            )
        mapping, missing, extra, differences = _map_words_to_tokens(normalized.spoken_tokens, words)
        mapped_words = [word.model_copy(update={"source_spoken_token_ids": mapping.get(word.word_id, [])}) for word in words]
        warnings = [item["reason_code"] for item in differences]
        if skipped_empty_word_count:
            warnings.append("WHITELISTED_FORCED_ALIGNMENT_EMPTY_WORD_ENTRY")
        if missing:
            warnings.append("FORCED_ALIGNMENT_MISSING_SPOKEN_TOKEN")
        if extra:
            warnings.append("FORCED_ALIGNMENT_EXTRA_WORD")
        characters, skipped_zero_duration_character_count = self._forced_characters(
            response.get("characters"), audio_duration_ms
        )
        if skipped_zero_duration_character_count:
            warnings.append(
                "WHITELISTED_FORCED_ALIGNMENT_ZERO_DURATION_CHARACTER_ENTRY"
            )
        headers = {key.casefold(): value for key, value in (response_headers or {}).items()}
        provider_request_id = (
            str(
                response.get("request_id")
                or headers.get("request-id")
                or headers.get("x-request-id")
                or ""
            ).strip()
            or None
        )
        provider_request_id_availability = (
            "PRESENT" if provider_request_id else "NOT_EXPOSED_BY_ENDPOINT"
        )
        if provider_request_id is None:
            warnings.append("FORCED_ALIGNMENT_REQUEST_ID_NOT_EXPOSED_BY_ENDPOINT")
        payload = {
            "provider_key": "elevenlabs_forced_alignment",
            "provider_request_id": provider_request_id,
            "provider_request_id_availability": provider_request_id_availability,
            "audio_asset_ref": audio_asset_ref,
            "audio_duration_ms": audio_duration_ms,
            "spoken_text_hash": normalized.spoken_text_hash,
            "words": [item.model_dump(mode="json") for item in mapped_words],
            "characters": [item.model_dump(mode="json") for item in characters],
            "alignment_loss": float(response.get("alignment_loss", response.get("loss"))) if response.get("alignment_loss", response.get("loss")) is not None else None,
            "transcript_loss": float(response["transcript_loss"]) if response.get("transcript_loss") is not None else None,
            "missing_tokens": missing,
            "extra_words": extra,
            "warnings": sorted(set(warnings)),
            "verification_status": "BLOCK" if missing or extra else "PASS",
        }
        return ForcedAlignmentEvidence(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def _time_ms(item: dict[str, Any], key: str) -> int:
        if item.get(f"{key}_ms") is not None:
            return round(float(item[f"{key}_ms"]))
        if item.get(key) is None:
            raise ValueError("FORCED_ALIGNMENT_TIME_MISSING")
        return round(float(item[key]) * 1000)

    @classmethod
    def _forced_characters(
        cls, raw: Any, audio_duration_ms: int
    ) -> tuple[list[CharacterAlignment], int]:
        if raw is None:
            return [], 0
        if isinstance(raw, dict):
            characters = raw.get("characters") or []
            starts = (
                raw.get("character_start_times_seconds")
                or raw.get("character_start_times")
                or []
            )
            ends = (
                raw.get("character_end_times_seconds")
                or raw.get("character_end_times")
                or []
            )
            if not (
                isinstance(characters, list)
                and isinstance(starts, list)
                and isinstance(ends, list)
            ):
                raise ValueError("FORCED_ALIGNMENT_CHARACTERS_SHAPE_INVALID")
            if len(characters) != len(starts) or len(characters) != len(ends):
                raise ValueError("FORCED_ALIGNMENT_CHARACTERS_LENGTH_MISMATCH")
            raw = [
                {"text": character, "start": start, "end": end}
                for character, start, end in zip(
                    characters, starts, ends, strict=True
                )
            ]
        if not isinstance(raw, list):
            raise ValueError("FORCED_ALIGNMENT_CHARACTERS_SHAPE_INVALID")
        result: list[CharacterAlignment] = []
        last_start = -1
        skipped_zero_duration = 0
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError("FORCED_ALIGNMENT_CHARACTERS_SHAPE_INVALID")
            start_ms = cls._time_ms(item, "start")
            end_ms = cls._time_ms(item, "end")
            if start_ms < last_start:
                raise ValueError("TEMPORAL_ALIGNMENT_NON_MONOTONIC")
            if start_ms < 0 or end_ms < start_ms or end_ms > audio_duration_ms:
                raise ValueError("TEMPORAL_ALIGNMENT_AUDIO_BOUNDS_INVALID")
            last_start = start_ms
            if end_ms == start_ms:
                skipped_zero_duration += 1
                continue
            result.append(
                CharacterAlignment(
                    character_index=index,
                    character=str(item.get("text") or ""),
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            )
        return result, skipped_zero_duration


def _map_words_to_tokens(
    tokens: list[SpokenToken],
    words: list[AlignedWord],
) -> tuple[dict[str, list[str]], list[str], list[str], list[dict[str, Any]]]:
    mapping: dict[str, list[str]] = {}
    missing: list[str] = []
    extra: list[str] = []
    differences: list[dict[str, Any]] = []
    token_index = 0
    word_index = 0
    while token_index < len(tokens) and word_index < len(words):
        token = tokens[token_index]
        word = words[word_index]
        word_key = _comparison_key(word.text)
        if token.comparison_key == word_key:
            mapping[word.word_id] = [token.token_id]
            if token.text != word.text:
                differences.append({
                    "reason_code": "WHITELISTED_ORTHOGRAPHIC_DIFFERENCE",
                    "forced_text": word.text,
                    "spoken_token_ids": [token.token_id],
                })
            token_index += 1
            word_index += 1
            continue
        matched = False
        for count in range(2, min(4, len(tokens) - token_index) + 1):
            token_group = tokens[token_index: token_index + count]
            if "".join(item.comparison_key for item in token_group) == word_key:
                mapping[word.word_id] = [item.token_id for item in token_group]
                differences.append({
                    "reason_code": "WHITELISTED_TOKEN_COMPACTION",
                    "forced_text": word.text,
                    "spoken_token_ids": mapping[word.word_id],
                })
                token_index += count
                word_index += 1
                matched = True
                break
        if matched:
            continue
        for count in range(2, min(4, len(words) - word_index) + 1):
            word_group = words[word_index: word_index + count]
            if "".join(_comparison_key(item.text) for item in word_group) == token.comparison_key:
                for grouped in word_group:
                    mapping[grouped.word_id] = [token.token_id]
                differences.append({
                    "reason_code": "WHITELISTED_TOKEN_EXPANSION",
                    "forced_word_ids": [item.word_id for item in word_group],
                    "spoken_token_ids": [token.token_id],
                })
                token_index += 1
                word_index += count
                matched = True
                break
        if matched:
            continue
        remaining_keys = {item.comparison_key for item in tokens[token_index + 1:]}
        if word_key in remaining_keys:
            missing.append(token.token_id)
            token_index += 1
        else:
            extra.append(word.text)
            word_index += 1
    missing.extend(item.token_id for item in tokens[token_index:])
    extra.extend(item.text for item in words[word_index:])
    return mapping, sorted(set(missing)), extra, differences


class NarrationAlignmentReconciler:
    def __init__(self, *, conflict_warning_ms: int = 80, conflict_block_ms: int = 250):
        self.conflict_warning_ms = conflict_warning_ms
        self.conflict_block_ms = conflict_block_ms

    def reconcile(
        self,
        *,
        normalized: SpokenTextNormalized,
        timing_seed: NarrationTimingSeed | None,
        forced_alignment: ForcedAlignmentEvidence | None,
        audio_asset_ref: str,
        audio_duration_ms: int,
    ) -> VerifiedNarrationAlignment:
        reason_codes: list[str] = []
        if timing_seed is None or not timing_seed.timing_available:
            reason_codes.append("TEMPORAL_PROVIDER_TIMING_MISSING")
        if forced_alignment is None:
            reason_codes.append("TEMPORAL_FORCED_ALIGNMENT_MISSING")
        if timing_seed and timing_seed.spoken_text_hash != normalized.spoken_text_hash:
            reason_codes.append("TEMPORAL_SPOKEN_TEXT_MISMATCH")
        if forced_alignment and forced_alignment.spoken_text_hash != normalized.spoken_text_hash:
            reason_codes.append("TEMPORAL_SPOKEN_TEXT_MISMATCH")
        if timing_seed and (timing_seed.audio_asset_ref != audio_asset_ref or timing_seed.audio_duration_ms != audio_duration_ms):
            reason_codes.append("TEMPORAL_AUDIO_DURATION_MISMATCH")
        if forced_alignment and (forced_alignment.audio_asset_ref != audio_asset_ref or forced_alignment.audio_duration_ms != audio_duration_ms):
            reason_codes.append("TEMPORAL_AUDIO_DURATION_MISMATCH")

        provider_by_token = self._provider_word_spans(normalized, timing_seed) if timing_seed and timing_seed.timing_available else {}
        forced_by_token: dict[str, tuple[int, int]] = {}
        normalization_differences: list[dict[str, Any]] = []
        if forced_alignment:
            for word in forced_alignment.words:
                for token_id in word.source_spoken_token_ids:
                    current = forced_by_token.get(token_id)
                    forced_by_token[token_id] = (
                        min(current[0], word.start_ms) if current else word.start_ms,
                        max(current[1], word.end_ms) if current else word.end_ms,
                    )
                if len(word.source_spoken_token_ids) != 1:
                    normalization_differences.append({
                        "reason_code": "WHITELISTED_TOKEN_COMPACTION",
                        "forced_word_id": word.word_id,
                        "spoken_token_ids": word.source_spoken_token_ids,
                    })
            normalization_differences.extend(
                {"reason_code": warning}
                for warning in forced_alignment.warnings
                if warning.startswith("WHITELISTED_")
            )
            if forced_alignment.verification_status == "BLOCK":
                reason_codes.extend(forced_alignment.warnings)

        verified_words: list[VerifiedNarrationWord] = []
        conflicts: list[TimingConflict] = []
        missing_tokens: list[str] = []
        last_end = -1
        for token in normalized.spoken_tokens:
            provider_span = provider_by_token.get(token.token_id)
            forced_span = forced_by_token.get(token.token_id)
            if provider_span is None or forced_span is None:
                missing_tokens.append(token.token_id)
                continue
            start_ms, end_ms = provider_span
            if start_ms < last_end or end_ms <= start_ms:
                reason_codes.append("TEMPORAL_ALIGNMENT_NON_MONOTONIC")
            if end_ms > audio_duration_ms:
                reason_codes.append("TEMPORAL_AUDIO_DURATION_MISMATCH")
            delta = max(abs(start_ms - forced_span[0]), abs(end_ms - forced_span[1]))
            word_reasons = ["PROVIDER_TIMING_PRIMARY_SEED", "FORCED_ALIGNMENT_VERIFIED"]
            confidence = 1.0
            if delta > self.conflict_warning_ms:
                conflicts.append(
                    TimingConflict(
                        spoken_token_ids=[token.token_id],
                        provider_start_ms=start_ms,
                        provider_end_ms=end_ms,
                        forced_start_ms=forced_span[0],
                        forced_end_ms=forced_span[1],
                        max_delta_ms=delta,
                        reason_code="TEMPORAL_ALIGNMENT_TIMING_CONFLICT",
                    )
                )
                word_reasons.append("TIMING_CONFLICT_RECORDED")
                confidence = max(0.0, 1.0 - (delta / max(audio_duration_ms, 1)))
            if delta > self.conflict_block_ms:
                reason_codes.append("TEMPORAL_HIGH_ALIGNMENT_CONFLICT")
            verified_words.append(
                VerifiedNarrationWord(
                    word_id=f"verified-{len(verified_words) + 1:04d}",
                    text=token.text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    source_spoken_token_ids=[token.token_id],
                    provider_start_ms=start_ms,
                    provider_end_ms=end_ms,
                    forced_start_ms=forced_span[0],
                    forced_end_ms=forced_span[1],
                    confidence=round(confidence, 6),
                    reason_codes=word_reasons,
                )
            )
            last_end = end_ms
        extra_tokens = list(forced_alignment.extra_words) if forced_alignment else []
        if missing_tokens:
            reason_codes.append("TEMPORAL_TOKEN_COVERAGE_GAP")
        if extra_tokens:
            reason_codes.append("TEMPORAL_UNEXPLAINED_EXTRA_TOKEN")
        covered = {token_id for word in verified_words for token_id in word.source_spoken_token_ids}
        token_coverage = len(covered) / len(normalized.spoken_tokens)
        if token_coverage != 1.0:
            reason_codes.append("TEMPORAL_TOKEN_COVERAGE_GAP")
        status = "BLOCK" if reason_codes else "PASS"
        confidence = min((word.confidence for word in verified_words), default=0.0)
        if status == "PASS" and token_coverage == 1.0:
            reason_codes.append("PROVIDER_SEED_FORCED_ALIGNMENT_RECONCILED")
        payload = {
            "spoken_text_hash": normalized.spoken_text_hash,
            "audio_asset_ref": audio_asset_ref,
            "audio_duration_ms": audio_duration_ms,
            "verified_words": [item.model_dump(mode="json") for item in verified_words],
            "provider_seed_ref": f"narration-timing-seed:{timing_seed.content_hash}" if timing_seed else None,
            "forced_alignment_ref": f"forced-alignment:{forced_alignment.content_hash}" if forced_alignment else None,
            "token_coverage": round(token_coverage, 6),
            "missing_tokens": sorted(set(missing_tokens)),
            "extra_tokens": extra_tokens,
            "normalization_only_differences": normalization_differences,
            "timing_conflicts": [item.model_dump(mode="json") for item in conflicts],
            "alignment_confidence": round(confidence, 6),
            "reconciliation_reason_codes": sorted(set(reason_codes)),
            "verification_status": status,
        }
        return VerifiedNarrationAlignment(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def _provider_word_spans(
        normalized: SpokenTextNormalized,
        seed: NarrationTimingSeed,
    ) -> dict[str, tuple[int, int]]:
        by_index = {item.character_index: item for item in seed.normalized_character_alignment}
        result: dict[str, tuple[int, int]] = {}
        for token in normalized.spoken_tokens:
            characters = [by_index[index] for index in range(token.spoken_span.start, token.spoken_span.end) if index in by_index]
            if len(characters) != token.spoken_span.end - token.spoken_span.start:
                continue
            result[token.token_id] = (min(item.start_ms for item in characters), max(item.end_ms for item in characters))
        return result


class CanonicalMediaTimelineCompiler:
    def compile(
        self,
        *,
        project_id: str,
        package_id: str,
        channel_id: str,
        script_revision_id: str,
        spoken_text_revision_id: str,
        tts_request_id: str,
        normalized: SpokenTextNormalized,
        alignment: VerifiedNarrationAlignment,
        segments: list[EditorialSegmentInput],
    ) -> CanonicalMediaTimeline:
        if alignment.verification_status != "PASS" or alignment.token_coverage != 1.0:
            raise ValueError("TEMPORAL_VERIFIED_ALIGNMENT_REQUIRED")
        if alignment.spoken_text_hash != normalized.spoken_text_hash:
            raise ValueError("TEMPORAL_SPOKEN_TEXT_MISMATCH")
        token_by_id = {item.token_id: item for item in normalized.spoken_tokens}
        word_by_token = {
            token_id: word
            for word in alignment.verified_words
            for token_id in word.source_spoken_token_ids
        }
        if not segments:
            raise ValueError("TEMPORAL_SEGMENTS_REQUIRED")
        seen_tokens: set[str] = set()
        compiled: list[CanonicalTimelineSegment] = []
        previous_scene_end = -1
        for index, segment in enumerate(segments):
            if any(token_id not in token_by_id or token_id not in word_by_token for token_id in segment.spoken_token_ids):
                raise ValueError("TEMPORAL_SEGMENT_TOKEN_UNKNOWN")
            if seen_tokens.intersection(segment.spoken_token_ids):
                raise ValueError("TEMPORAL_SEGMENT_TOKEN_OVERLAP")
            seen_tokens.update(segment.spoken_token_ids)
            tokens = sorted((token_by_id[token_id] for token_id in segment.spoken_token_ids), key=lambda item: item.spoken_span.start)
            words = [word_by_token[item.token_id] for item in tokens]
            audio_start, audio_end = words[0].start_ms, words[-1].end_ms
            scene_start = audio_start
            scene_end = alignment.audio_duration_ms if index == len(segments) - 1 else audio_end
            if scene_start < previous_scene_end:
                raise ValueError("TEMPORAL_SCENE_OVERLAP")
            phrases = self._phrase_boundaries(normalized, tokens, word_by_token, segment.segment_id)
            provenance = [
                *segment.source_provenance,
                {"type": "verified_narration_alignment", "ref": f"verified-alignment:{alignment.content_hash}"},
                {"type": "timing_derivation", "value": "VERIFIED_WORD_SPANS"},
            ]
            compiled.append(
                CanonicalTimelineSegment(
                    segment_id=segment.segment_id,
                    editorial_span=segment.editorial_span,
                    spoken_span=TextSpan(start=tokens[0].spoken_span.start, end=tokens[-1].spoken_span.end),
                    display_span=segment.display_span,
                    spoken_token_ids=[item.token_id for item in tokens],
                    audio_start_ms=audio_start,
                    audio_end_ms=audio_end,
                    words=words,
                    phrase_boundaries=phrases,
                    scene_start_ms=scene_start,
                    scene_end_ms=scene_end,
                    target_scene_duration_ms=scene_end - scene_start,
                    motion_intent=segment.motion_intent,
                    alignment_confidence=min(word.confidence for word in words),
                    source_provenance=provenance,
                )
            )
            previous_scene_end = scene_end
        expected_tokens = {item.token_id for item in normalized.spoken_tokens}
        if seen_tokens != expected_tokens:
            raise ValueError("TEMPORAL_TOKEN_COVERAGE_GAP")
        payload = {
            "timeline_version": TIMELINE_VERSION,
            "project_id": project_id,
            "package_id": package_id,
            "channel_id": channel_id,
            "script_revision_id": script_revision_id,
            "spoken_text_revision_id": spoken_text_revision_id,
            "tts_request_id": tts_request_id,
            "audio_asset_id": alignment.audio_asset_ref,
            "audio_duration_ms": alignment.audio_duration_ms,
            "provider_timing_seed_ref": alignment.provider_seed_ref,
            "forced_alignment_ref": alignment.forced_alignment_ref,
            "verified_alignment_ref": f"verified-alignment:{alignment.content_hash}",
            "segments": [item.model_dump(mode="json") for item in compiled],
            "qc_metrics": {
                "spoken_token_coverage": alignment.token_coverage,
                "alignment_confidence": alignment.alignment_confidence,
                "timing_source": "VERIFIED_FINAL_NARRATION_AUDIO",
                "scene_anchor_count": len(compiled),
                "estimated_timing_used": False,
            },
            "compilation_warnings": [],
        }
        if not payload["provider_timing_seed_ref"] or not payload["forced_alignment_ref"]:
            raise ValueError("TEMPORAL_STRICT_EVIDENCE_REF_MISSING")
        return CanonicalMediaTimeline(**payload, timeline_hash=stable_hash(payload))

    @staticmethod
    def _phrase_boundaries(
        normalized: SpokenTextNormalized,
        tokens: list[SpokenToken],
        word_by_token: dict[str, VerifiedNarrationWord],
        segment_id: str,
    ) -> list[PhraseBoundary]:
        phrases: list[PhraseBoundary] = []
        current: list[SpokenToken] = []
        for index, token in enumerate(tokens):
            current.append(token)
            next_start = tokens[index + 1].spoken_span.start if index + 1 < len(tokens) else token.spoken_span.end + 1
            separator = normalized.spoken_text[token.spoken_span.end:next_start]
            if re.search(r"[.!?;:]", separator) or index == len(tokens) - 1:
                phrase_words = [word_by_token[item.token_id] for item in current]
                phrases.append(
                    PhraseBoundary(
                        phrase_id=f"{segment_id}-phrase-{len(phrases) + 1:02d}",
                        spoken_token_ids=[item.token_id for item in current],
                        audio_start_ms=phrase_words[0].start_ms,
                        audio_end_ms=phrase_words[-1].end_ms,
                        boundary_reason="PUNCTUATION" if re.search(r"[.!?;:]", separator) else "SEGMENT_END",
                    )
                )
                current = []
        return phrases


class TemporalAuthorityGate:
    def evaluate(
        self,
        *,
        normalized: SpokenTextNormalized,
        final_audio: FinalNarrationAudio | list[FinalNarrationAudio] | None,
        alignment: VerifiedNarrationAlignment | None,
        timeline: CanonicalMediaTimeline | None,
    ) -> TemporalAuthorityGateResult:
        reasons: list[str] = []
        audio_items = [] if final_audio is None else final_audio if isinstance(final_audio, list) else [final_audio]
        final_items = [item for item in audio_items if item.is_final]
        if not final_items:
            reasons.append("TEMPORAL_AUDIO_MISSING")
        if len(final_items) > 1:
            reasons.append("TEMPORAL_MULTIPLE_FINAL_AUDIO_AUTHORITIES")
        audio = final_items[0] if len(final_items) == 1 else None
        if alignment is None:
            reasons.extend(["TEMPORAL_PROVIDER_TIMING_MISSING", "TEMPORAL_FORCED_ALIGNMENT_MISSING"])
        else:
            if alignment.spoken_text_hash != normalized.spoken_text_hash:
                reasons.append("TEMPORAL_SPOKEN_TEXT_MISMATCH")
            if not alignment.provider_seed_ref:
                reasons.append("TEMPORAL_PROVIDER_TIMING_MISSING")
            if not alignment.forced_alignment_ref:
                reasons.append("TEMPORAL_FORCED_ALIGNMENT_MISSING")
            if alignment.token_coverage != 1.0 or alignment.missing_tokens or alignment.extra_tokens:
                reasons.append("TEMPORAL_TOKEN_COVERAGE_GAP")
            if alignment.verification_status != "PASS":
                reasons.extend(alignment.reconciliation_reason_codes)
            previous_end = -1
            for word in alignment.verified_words:
                if word.start_ms < previous_end or word.end_ms <= word.start_ms:
                    reasons.append("TEMPORAL_ALIGNMENT_NON_MONOTONIC")
                previous_end = word.end_ms
            if audio and (alignment.audio_asset_ref != audio.audio_asset_ref or alignment.audio_duration_ms != audio.duration_ms):
                reasons.append("TEMPORAL_AUDIO_DURATION_MISMATCH")
        if timeline is None:
            reasons.append("TEMPORAL_PARALLEL_TIMELINE_DETECTED")
        else:
            if timeline.timeline_hash != stable_hash(timeline.model_dump(mode="json", exclude={"timeline_hash"})):
                reasons.append("TEMPORAL_PARALLEL_TIMELINE_DETECTED")
            if timeline.script_revision_id != normalized.script_revision_id:
                reasons.append("TEMPORAL_SPOKEN_TEXT_MISMATCH")
            if audio and (timeline.audio_asset_id != audio.audio_asset_ref or abs(timeline.audio_duration_ms - audio.duration_ms) > 20):
                reasons.append("TEMPORAL_AUDIO_DURATION_MISMATCH")
            if timeline.segments and abs(timeline.segments[-1].scene_end_ms - timeline.audio_duration_ms) > 20:
                reasons.append("TEMPORAL_AUDIO_DURATION_MISMATCH")
            if any(segment.timing_source != "VERIFIED_NARRATION_ALIGNMENT" for segment in timeline.segments):
                reasons.append("TEMPORAL_SCENE_ESTIMATE_USED")
            timeline_tokens = [token_id for segment in timeline.segments for token_id in segment.spoken_token_ids]
            if set(timeline_tokens) != {item.token_id for item in normalized.spoken_tokens} or len(timeline_tokens) != len(set(timeline_tokens)):
                reasons.append("TEMPORAL_TOKEN_COVERAGE_GAP")
            if "TEMPORAL_SCENE_ESTIMATE_USED" in timeline.compilation_warnings:
                reasons.append("TEMPORAL_SCENE_ESTIMATE_USED")
            if "TEMPORAL_PARALLEL_TIMELINE_DETECTED" in timeline.compilation_warnings:
                reasons.append("TEMPORAL_PARALLEL_TIMELINE_DETECTED")
        reasons = sorted(set(reasons))
        status = "BLOCK" if reasons else "PASS"
        payload = {
            "gate_status": status,
            "block_reasons": reasons,
            "exact_next_action": "COMPILE_DOWNSTREAM_FROM_CANONICAL_MEDIA_TIMELINE" if status == "PASS" else "REPAIR_TEMPORAL_EVIDENCE_AND_RECOMPILE",
        }
        return TemporalAuthorityGateResult(**payload, content_hash=stable_hash(payload))


class CanonicalTimelineWorkspaceStore:
    """Atomic local manifest persistence; never invokes Drive or another provider."""

    def persist(self, *, workspace_root: Path, timeline: CanonicalMediaTimeline) -> Path:
        if timeline.timeline_hash != stable_hash(timeline.model_dump(mode="json", exclude={"timeline_hash"})):
            raise ValueError("TEMPORAL_TIMELINE_HASH_MISMATCH")
        manifests = workspace_root.resolve() / "manifests"
        manifests.mkdir(parents=True, exist_ok=True)
        target = manifests / "canonical_media_timeline.json"
        part = target.with_suffix(".json.part")
        part.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
        os.replace(part, target)
        return target


class CanonicalTimelineArtifactPersistenceService:
    """Persists through generic Artifact/ArtifactVersion storage; no schema is added."""

    # Extend the existing narration-timeline artifact lineage instead of creating
    # a duplicate database/domain artifact type.
    artifact_type = "narration_timeline"

    def __init__(self, session: Session):
        self.session = session

    def persist(
        self,
        *,
        project_id: uuid.UUID,
        timeline: CanonicalMediaTimeline,
        created_by_user_id: uuid.UUID,
    ) -> ArtifactVersion:
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise ValueError("VIDEO_PROJECT_NOT_FOUND")
        artifact = self.session.scalars(
            select(Artifact).where(Artifact.video_project_id == project_id, Artifact.artifact_type == self.artifact_type)
        ).one_or_none()
        service = ArtifactService(self.session)
        if artifact is None:
            artifact = service.create_artifact(
                data=ArtifactCreate(
                    video_project_id=project_id,
                    artifact_type=self.artifact_type,
                    created_by_user_id=created_by_user_id,
                ),
                correlation_id="cqr1a-canonical-media-timeline",
            )
        version_count = self.session.scalar(
            select(func.count(ArtifactVersion.id)).where(ArtifactVersion.artifact_id == artifact.id)
        ) or 0
        return service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=artifact.current_version_id if version_count else None,
                content=timeline.model_dump(mode="json"),
                created_by_user_id=created_by_user_id,
                context_refs=[
                    {"type": "video_project", "id": str(project_id)},
                    {"type": "package", "id": timeline.package_id},
                ],
                evidence_refs=[
                    {"type": "provider_timing_seed", "ref": timeline.provider_timing_seed_ref},
                    {"type": "forced_alignment", "ref": timeline.forced_alignment_ref},
                    {"type": "verified_alignment", "ref": timeline.verified_alignment_ref},
                ],
                source_manifest={"timeline_hash": timeline.timeline_hash, "timing_source": "FINAL_NARRATION_AUDIO"},
            ),
            correlation_id="cqr1a-canonical-media-timeline-version",
        )


def elevenlabs_temporal_permission_readiness(settings: Settings | None = None) -> dict[str, bool | str]:
    settings = settings or get_settings()
    return {
        "ELEVENLABS_TTS_CONFIGURED": bool(
            settings.elevenlabs_api_key and settings.elevenlabs_voice_id and settings.elevenlabs_model_id
        ),
        "ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED": (
            settings.elevenlabs_forced_alignment_permission_confirmed
            if settings.elevenlabs_forced_alignment_permission_confirmed is not None
            else "unknown"
        ),
    }


def fixture_alignment_response(normalized: SpokenTextNormalized, *, duration_ms: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate deterministic local fixture payloads; this is not provider verification."""
    character_count = len(normalized.spoken_text)
    step = duration_ms / max(character_count, 1)
    starts = [round(index * step, 6) / 1000 for index in range(character_count)]
    ends = [round((index + 1) * step, 6) / 1000 for index in range(character_count)]
    alignment = {
        "characters": list(normalized.spoken_text),
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
    }
    forced_words = []
    for token in normalized.spoken_tokens:
        forced_words.append(
            {
                "text": token.text,
                "start": starts[token.spoken_span.start],
                "end": ends[token.spoken_span.end - 1],
                "type": "word",
                "loss": 0.01,
            }
        )
    provider = {
        "request_id": "fixture-tts-request",
        "audio_base64": "fixture-placeholder-not-audio",
        "alignment": alignment,
        "normalized_alignment": alignment,
    }
    forced = {
        "request_id": "fixture-forced-alignment-request",
        "words": forced_words,
        "alignment_loss": 0.01,
        "transcript_loss": 0.0,
    }
    return provider, forced


def run_cqr1a_fixture_rehearsal(workspace_root: Path) -> dict[str, Any]:
    """Compile the complete CQR1-A flow from generated local fixtures only."""
    source_text = (
        "Dr. Lee says VCOS version 2 budgets $1,250.50 on 2026-07-14. "
        "AI improves 12.5% across 3-5 teams at vcos.ai."
    )
    normalizer = SpokenTextNormalizer()
    normalized = normalizer.normalize(
        script_revision_id="fixture-script-revision-1",
        source_text=source_text,
        pronunciation_dictionary={"VCOS": "V C O S"},
        pronunciation_dictionary_refs=["fixture-pronunciation-dictionary-v1"],
    )
    duration_ms = max(4_000, len(normalized.spoken_text) * 45)
    provider_fixture, forced_fixture = fixture_alignment_response(normalized, duration_ms=duration_ms)
    timing_seed = ElevenLabsTimingResponseParser().parse(
        response=provider_fixture,
        normalized=normalized,
        audio_asset_ref="fixture://audio/final-narration.wav",
        audio_duration_ms=duration_ms,
        model_id="fixture-eleven-model",
        voice_id="fixture-eleven-voice",
        voice_settings={"stability": 0.55},
        pronunciation_dictionary_refs=normalized.pronunciation_dictionary_refs,
    )
    forced_request = ElevenLabsForcedAlignmentRequestBuilder().build(
        audio_asset_ref=timing_seed.audio_asset_ref,
        normalized=normalized,
    )
    fixture_response = FixtureOnlyAlignmentTransport().execute(
        request=forced_request,
        fixture_response=forced_fixture,
    )
    forced = ElevenLabsForcedAlignmentResponseParser().parse(
        response=fixture_response,
        normalized=normalized,
        audio_asset_ref=timing_seed.audio_asset_ref,
        audio_duration_ms=duration_ms,
    )
    verified = NarrationAlignmentReconciler().reconcile(
        normalized=normalized,
        timing_seed=timing_seed,
        forced_alignment=forced,
        audio_asset_ref=timing_seed.audio_asset_ref,
        audio_duration_ms=duration_ms,
    )
    timeline = CanonicalMediaTimelineCompiler().compile(
        project_id="cqr1a-fixture-project",
        package_id="cqr1a-fixture-package",
        channel_id="small-team-ai",
        script_revision_id=normalized.script_revision_id,
        spoken_text_revision_id=normalized.content_hash,
        tts_request_id="fixture-tts-request",
        normalized=normalized,
        alignment=verified,
        segments=[
            EditorialSegmentInput(
                segment_id="fixture-scene-1",
                editorial_span=TextSpan(start=0, end=len(source_text)),
                spoken_token_ids=[item.token_id for item in normalized.spoken_tokens],
                motion_intent="FIXTURE_ONLY_TIMING_ANCHOR",
                source_provenance=[{"type": "fixture", "provider_verified": False}],
            )
        ],
    )
    final_audio_payload = {
        "audio_asset_ref": timing_seed.audio_asset_ref,
        "duration_ms": duration_ms,
        "is_final": True,
    }
    final_audio = FinalNarrationAudio(**final_audio_payload, content_hash=stable_hash(final_audio_payload))
    gate = TemporalAuthorityGate().evaluate(
        normalized=normalized,
        final_audio=final_audio,
        alignment=verified,
        timeline=timeline,
    )
    root = workspace_root.resolve()
    manifests = root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    CanonicalTimelineWorkspaceStore().persist(workspace_root=root, timeline=timeline)
    artifacts = {
        "spoken_text_normalized.json": normalized.model_dump(mode="json"),
        "narration_timing_seed.json": timing_seed.model_dump(mode="json"),
        "forced_alignment_evidence.json": forced.model_dump(mode="json"),
        "verified_narration_alignment.json": verified.model_dump(mode="json"),
        "temporal_authority_gate.json": gate.model_dump(mode="json"),
    }
    for filename, payload in artifacts.items():
        target = manifests / filename
        part = target.with_suffix(target.suffix + ".part")
        part.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(part, target)
    summary = {
        "rehearsal": "CQR1A_LOCAL_FIXTURE_ONLY",
        "gate_status": gate.gate_status,
        "normalization_hash": normalized.content_hash,
        "verified_alignment_hash": verified.content_hash,
        "timeline_hash": timeline.timeline_hash,
        "token_coverage": verified.token_coverage,
        "provider_call_made": False,
        "network_call_made": False,
        "drive_call_made": False,
        "youtube_call_made": False,
        "production_render_made": False,
        "fixture_is_real_provider_verification": False,
        "created_entities": {
            "FinalMediaRef": 0,
            "HumanUploadTask": 0,
            "UploadedVideo": 0,
            "ChannelProfileVersion": 0,
            "frozen_context": 0,
        },
        "failure_cases_covered_by_tests": [
            "missing spoken word",
            "extra spoken word",
            "provider timing missing",
            "forced alignment missing",
            "high alignment conflict",
            "non-monotonic word time",
            "word beyond audio duration",
            "scene using estimated timing",
            "timeline/audio duration mismatch",
            "parallel caption timeline conflict",
        ],
    }
    summary_path = root / "cqr1a_fixture_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence


def caption_render_payload(cues: Sequence[Any]) -> list[dict[str, Any]]:
    """Return only fields that can change the burned-in caption result."""

    payload: list[dict[str, Any]] = []
    for cue in cues:
        value = cue.model_dump(mode="json") if hasattr(cue, "model_dump") else dict(cue)
        payload.append(
            {
                "cue_id": str(value["cue_id"]),
                "caption_start_ms": int(value["caption_start_ms"]),
                "caption_end_ms": int(value["caption_end_ms"]),
                "caption_lines": [str(line) for line in value["caption_lines"]],
                "spoken_token_ids": [str(token_id) for token_id in value["spoken_token_ids"]],
                "timing_source": str(value["timing_source"]),
            }
        )
    return payload


def resolved_caption_render_style(
    *,
    policy: Any,
    format_policy: Any,
    aspect_ratio: str,
    policy_hash: str,
) -> dict[str, Any]:
    """Freeze the exact versioned libass style used by preflight and final render."""

    return {
        "style_version": "cqr1-caption-ass-style/v1.0.0",
        "policy_ref": policy.policy_ref,
        "policy_version": policy.policy_version,
        "policy_hash": policy_hash,
        "aspect_ratio": aspect_ratio,
        "font_family": policy.font_family,
        "font_scale": round(sum(format_policy.font_scale_pass) / 2, 6),
        "primary_colour": policy.primary_colour,
        "secondary_colour": "&H000000FF",
        "outline_colour": policy.outline_colour,
        "back_colour": "&H80000000",
        "border_style": policy.border_style,
        "outline_ratio": policy.outline_ratio,
        "shadow_ratio": policy.shadow_ratio,
        "alignment": policy.alignment,
        "bottom_safe_margin": format_policy.bottom_safe_margin_pass,
        "margin_left_ratio": 0.0,
        "margin_right_ratio": 0.0,
    }


def build_caption_ass_document(
    *,
    cues: Sequence[Any],
    frame_width: int,
    frame_height: int,
    render_style: dict[str, Any],
    force_event_window_ms: tuple[int, int] | None = None,
) -> str:
    if frame_width <= 0 or frame_height <= 0 or not cues:
        raise ValueError("CAPTION_ASS_INPUT_INVALID")
    _validate_style(render_style)
    font_size = max(1, round(min(frame_width, frame_height) * float(render_style["font_scale"])))
    outline = round(font_size * float(render_style["outline_ratio"]), 3)
    shadow = round(font_size * float(render_style["shadow_ratio"]), 3)
    # ASS MarginV is applied before the outline/shadow raster footprint.  Add
    # that measured style footprint (plus one antialiasing pixel) so the actual
    # non-empty bbox, rather than only the text anchor, clears the policy safe
    # margin.  This remains resolution-relative and uses the frozen style.
    margin_v = math.ceil(frame_height * float(render_style["bottom_safe_margin"])) + math.ceil(
        outline + shadow + 1
    )
    margin_l = round(frame_width * float(render_style.get("margin_left_ratio", 0.0)))
    margin_r = round(frame_width * float(render_style.get("margin_right_ratio", 0.0)))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {frame_width}\n"
        f"PlayResY: {frame_height}\n"
        f"LayoutResX: {frame_width}\n"
        f"LayoutResY: {frame_height}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: CQR1,{render_style['font_family']},{font_size},{render_style['primary_colour']},"
        f"{render_style['secondary_colour']},{render_style['outline_colour']},"
        f"{render_style['back_colour']},0,0,0,0,100,100,0,0,{render_style['border_style']},"
        f"{outline},{shadow},{render_style['alignment']},{margin_l},{margin_r},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    rows: list[str] = []
    for cue in caption_render_payload(cues):
        lines = cue["caption_lines"]
        if not 1 <= len(lines) <= 2:
            raise ValueError("CANONICAL_CAPTION_CUE_INVALID")
        for line in lines:
            _validate_caption_line(line)
        start_ms, end_ms = (
            force_event_window_ms
            if force_event_window_ms is not None
            else (cue["caption_start_ms"], cue["caption_end_ms"])
        )
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("CANONICAL_CAPTION_CUE_INVALID")
        text = r"\N".join(lines)
        rows.append(
            f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},CQR1,,0,0,0,,{text}"
        )
    return header + "\n".join(rows) + "\n"


def write_caption_ass(
    path: Path,
    *,
    cues: Sequence[Any],
    frame_width: int,
    frame_height: int,
    render_style: dict[str, Any],
) -> None:
    path.write_text(
        build_caption_ass_document(
            cues=cues,
            frame_width=frame_width,
            frame_height=frame_height,
            render_style=render_style,
        ),
        encoding="utf-8",
    )


def _validate_style(style: dict[str, Any]) -> None:
    required = {
        "font_family",
        "font_scale",
        "primary_colour",
        "secondary_colour",
        "outline_colour",
        "back_colour",
        "border_style",
        "outline_ratio",
        "shadow_ratio",
        "alignment",
        "bottom_safe_margin",
    }
    if not required.issubset(style):
        raise ValueError("CAPTION_RENDER_STYLE_INCOMPLETE")
    font_family = str(style["font_family"])
    if not font_family or any(character in font_family for character in (",", "\r", "\n", "{", "}", "\\")):
        raise ValueError("CAPTION_ASS_FONT_FAMILY_UNSAFE")
    for key in ("primary_colour", "secondary_colour", "outline_colour", "back_colour"):
        value = str(style[key])
        if len(value) != 10 or not value.startswith("&H") or any(
            character not in "0123456789abcdefABCDEF" for character in value[2:]
        ):
            raise ValueError("CAPTION_ASS_COLOUR_INVALID")
    if not 0.01 <= float(style["font_scale"]) <= 0.10:
        raise ValueError("CAPTION_POLICY_GEOMETRY_INVALID")
    if not 0 <= float(style["bottom_safe_margin"]) <= 0.40:
        raise ValueError("CAPTION_POLICY_GEOMETRY_INVALID")
    if not 0 <= float(style["outline_ratio"]) <= 0.20 or not 0 <= float(style["shadow_ratio"]) <= 0.20:
        raise ValueError("CAPTION_POLICY_GEOMETRY_INVALID")
    if int(style["border_style"]) not in {1, 2, 3, 4} or not 1 <= int(style["alignment"]) <= 9:
        raise ValueError("CAPTION_RENDER_STYLE_INVALID")


def _validate_caption_line(line: str) -> None:
    if not line.strip() or any(character in line for character in ("\r", "\n", "\\", "{", "}")):
        raise ValueError("CAPTION_ASS_CONTROL_SEQUENCE_BLOCKED")


def _ass_time(milliseconds: int) -> str:
    centiseconds = max(0, int(round(milliseconds / 10)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"

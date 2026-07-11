from __future__ import annotations

from pathlib import Path

from app.contracts.asset_acquisition import MediaNormalizationManifest
from app.services.native_render_plan import stable_hash


FORBIDDEN_ARG_CHARACTERS = {"\x00", "\n", "\r", ";", "|", "&", "`"}


class MediaNormalizer:
    """Compile normalization argv only. AS1 never executes FFmpeg."""

    def compile_video_plan(
        self,
        *,
        input_asset_ref: str,
        input_asset_hash: str,
        input_path: Path,
        output_path: Path,
        width: int,
        height: int,
        fps: int = 30,
        trim_start_seconds: float = 0,
        trim_end_seconds: float | None = None,
        audio_policy: str = "REMOVE",
    ) -> MediaNormalizationManifest:
        if width <= 0 or height <= 0 or fps not in {24, 25, 30}:
            raise ValueError("VIDEO_NORMALIZATION_PROFILE_INVALID")
        if audio_policy not in {"REMOVE", "PRESERVE"}:
            raise ValueError("VIDEO_AUDIO_POLICY_INVALID")
        profile = {
            "media_type": "VIDEO",
            "width": width,
            "height": height,
            "fps": fps,
            "timebase": f"1/{fps}",
            "pixel_format": "yuv420p",
            "color_primaries": "bt709",
            "color_transfer": "bt709",
            "color_space": "bt709",
            "trim_start_seconds": trim_start_seconds,
            "trim_end_seconds": trim_end_seconds,
            "audio_policy": audio_policy,
        }
        argv = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(input_path),
            "-ss",
            str(trim_start_seconds),
        ]
        if trim_end_seconds is not None:
            if trim_end_seconds <= trim_start_seconds:
                raise ValueError("VIDEO_TRIM_RANGE_INVALID")
            argv.extend(["-to", str(trim_end_seconds)])
        argv.extend(
            [
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps={fps},format=yuv420p",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-an" if audio_policy == "REMOVE" else "-c:a",
            ]
        )
        if audio_policy == "PRESERVE":
            argv.append("aac")
        argv.append(str(output_path))
        return self._manifest(input_asset_ref, input_asset_hash, profile, argv, output_path, {"width": width, "height": height, "fps": fps, "pixel_format": "yuv420p", "color": "BT.709"})

    def compile_audio_plan(
        self,
        *,
        input_asset_ref: str,
        input_asset_hash: str,
        input_path: Path,
        output_path: Path,
        loudness_peak_policy_ref: str,
        target_duration_seconds: float | None = None,
    ) -> MediaNormalizationManifest:
        profile = {
            "media_type": "AUDIO",
            "sample_rate": 48000,
            "channels": 2,
            "channel_layout": "stereo",
            "loudness_peak_policy_ref": loudness_peak_policy_ref,
            "target_duration_seconds": target_duration_seconds,
            "duration_alignment": "TRIM_OR_PAD_TO_TIMELINE" if target_duration_seconds is not None else "PRESERVE",
        }
        argv = ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(input_path), "-ar", "48000", "-ac", "2"]
        if target_duration_seconds is not None:
            if target_duration_seconds <= 0:
                raise ValueError("AUDIO_TARGET_DURATION_INVALID")
            argv.extend(["-af", "apad", "-t", str(target_duration_seconds)])
        argv.extend(["-c:a", "pcm_s16le", str(output_path)])
        return self._manifest(input_asset_ref, input_asset_hash, profile, argv, output_path, {"sample_rate": 48000, "channels": 2, "channel_layout": "stereo", "duration_seconds": target_duration_seconds})

    @staticmethod
    def _manifest(input_ref: str, input_hash: str, profile: dict, argv: list[str], output_path: Path, shape: dict) -> MediaNormalizationManifest:
        if any(any(character in arg for character in FORBIDDEN_ARG_CHARACTERS) for arg in argv):
            raise ValueError("NORMALIZATION_ARGV_UNSAFE")
        payload = {
            "input_asset_ref": input_ref,
            "input_asset_hash": input_hash,
            "normalization_profile": profile,
            "sanitized_ffmpeg_argv_plan": argv,
            "output_path": str(output_path),
            "expected_output_shape": shape,
            "execution_allowed": False,
        }
        return MediaNormalizationManifest(**payload, manifest_hash=stable_hash(payload))

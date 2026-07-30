from __future__ import annotations

from pathlib import Path

from app.services.config_registry import content_hash
from app.services.v2_native_effects import (
    V2LocalNarrationRuntime,
    _resolve_local_narration_runtime,
)


def test_espeak_ng_command_and_journal_identity_are_exact() -> None:
    runtime = V2LocalNarrationRuntime(
        backend="ESPEAK_NG",
        binary="/usr/bin/espeak-ng",
        voice="en-us",
        rate_wpm=150,
    )
    output = Path("/app/var/v2-production/effects/command/canonical-narration.part.wav")
    script = "Exact approved production narration."

    command = runtime.build_command(output=output, script_text=script)

    assert runtime.output_suffix == ".wav"
    assert runtime.output_format == "WAV"
    assert command == [
        "/usr/bin/espeak-ng",
        "-v",
        "en-us",
        "-s",
        "150",
        "-w",
        str(output),
        script,
    ]
    assert runtime.journal_identity(command=command) == {
        "tts_backend": "ESPEAK_NG",
        "tts_binary": "/usr/bin/espeak-ng",
        "voice": "en-us",
        "rate_wpm": 150,
        "output_format": "WAV",
        "command_argv_hash": content_hash(command),
    }


def test_macos_say_command_remains_aiff_and_rate_bound() -> None:
    runtime = V2LocalNarrationRuntime(
        backend="MACOS_SAY",
        binary="/usr/bin/say",
        voice="Samantha",
        rate_wpm=150,
    )
    output = Path("/tmp/canonical-narration.part.aiff")

    assert runtime.build_command(
        output=output,
        script_text="Exact approved production narration.",
    ) == [
        "/usr/bin/say",
        "-v",
        "Samantha",
        "-r",
        "150",
        "-o",
        str(output),
        "Exact approved production narration.",
    ]


def test_espeak_ignores_legacy_macos_voice(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VCOS_V2_LOCAL_TTS_PATH", "/usr/bin/espeak-ng")
    monkeypatch.setenv("VCOS_V2_LOCAL_TTS_BACKEND", "espeak-ng")
    monkeypatch.setenv("VCOS_V2_LOCAL_TTS_VOICE", "Samantha")
    monkeypatch.delenv("VCOS_V2_ESPEAK_NG_VOICE", raising=False)
    monkeypatch.setenv("VCOS_V2_ESPEAK_NG_RATE_WPM", "155")

    runtime = _resolve_local_narration_runtime()

    assert runtime is not None
    assert runtime.backend == "ESPEAK_NG"
    assert runtime.voice == "en-us"
    assert runtime.rate_wpm == 155

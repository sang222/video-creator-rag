# Narration timing and alignment operations

## CQR1-A fixture workflow

The local rehearsal generates deterministic fixture responses and never opens a network transport:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
from pathlib import Path
from app.services.temporal_authority import run_cqr1a_fixture_rehearsal
print(run_cqr1a_fixture_rehearsal(Path("var/tmp/cqr1a-temporal-authority-fixture")))
PY
```

Expected evidence is under `var/tmp/cqr1a-temporal-authority-fixture/manifests/`. A valid rehearsal reports `gate_status=PASS`, `token_coverage=1.0`, `provider_call_made=false` and `fixture_is_real_provider_verification=false`.

Do not describe this rehearsal as ElevenLabs verification. No TTS, forced-alignment, Pexels, Veo, Drive, YouTube or render call occurs.

## ElevenLabs permission readiness

Before the separately authorized CQR1-D paid canary, an operator must open the restricted ElevenLabs API key in the ElevenLabs console and explicitly enable:

```text
Text to Speech: Access
Voices: Read
Models: Read
Forced Alignment: Access
```

Then set only the confirmation flag after checking the key permissions:

```text
ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true
```

CQR1-A does not change provider permissions and does not validate them over the network. Readiness exposes only:

```text
ELEVENLABS_TTS_CONFIGURED=true|false
ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true|false|unknown
```

Never log, echo or include the API key in an artifact. `unknown` is the safe default until the operator confirms the restricted key.

## Strict execution checks

Before any future repaired render:

1. Confirm the final narration audio ref and measured duration.
2. Parse `convert-with-timestamps` into `NarrationTimingSeed`.
3. Run forced alignment against that exact audio and `SpokenTextNormalized`.
4. Require reconciliation PASS and 100% token coverage.
5. Compile and persist `CanonicalMediaTimeline`.
6. Require `TemporalAuthorityGate=PASS`.
7. Compile `NativeRenderPlan` in `CANONICAL_STRICT` with the referenced timeline object.

There is no human override for missing final audio, missing provider timing, missing forced alignment or less than 100% token coverage. Repair evidence and recompile instead.

Official provider contract references: [Create speech with timing](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps) and [Create Forced Alignment](https://elevenlabs.io/docs/api-reference/forced-alignment/create/). These links document response shape only; following them does not authorize execution.

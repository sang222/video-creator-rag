# CQR1 creative-quality canary operations

## Scope

The only approved run is:

```text
run_id=pa1r-cqr1-20260714-paid-canary-001
purpose=CQR1_CONTROLLED_PAID_CANARY
production_eligible=false
not_publishable=true
```

It is an English-US, 28–40 second non-production explainer. Native graphics are
the backbone, with at most one selected Pexels clip and one eight-second Veo
hero. The visible label is `VCOS CQR1 NON-PRODUCTION CANARY`.

## Offline entry gate

No provider probe or call is allowed until all CQR1-B/C fixtures, golden media,
negative tests, technical and creative fixture QC, one Alembic head,
`compileall`, focused regressions, historical-hash checks and
`git diff --check` pass.

After offline PASS, static readiness must prove configured Pexels, ElevenLabs
and Gemini credentials; explicit ElevenLabs TTS/Voices/Models/Forced Alignment
permissions; Veo model access; and a connected Drive archive root. Only safe
booleans and redacted references may be recorded.

If Forced Alignment is false or unknown, preflight is `BLOCKED`, provider call
count remains zero and the operator must grant the four required permissions,
configure the approved voice/model, set
`ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true`, then rerun preflight.

## One-shot execution

The fresh atomic ledger permits one Pexels search flow/download, one complete
ElevenLabs `convert-with-timestamps`, one Forced Alignment call, one Veo submit
and output, and one Drive archive. Provider retries and alternate providers are
disabled. Total estimated cost may not exceed USD 3.00.

An attempt is consumed immediately before transport. After any consumed
attempt, failure stops that run. Repair is offline and requires a new run ID and
new approval; the same run is never patched and retried.

## QC, archive and human review

TechnicalMediaQC and CreativePerceptualMediaQC are separate. Technical PASS
never implies Creative PASS. Any creative `BLOCK` stops downstream work. A
policy-acceptable creative `REVIEW_REQUIRED` may be archived but can never be
promoted automatically.

The required Drive path, if archive is later authorized and verified, is:

```text
smoke_tests/2026-07-14/cqr1/pa1r-cqr1-20260714-paid-canary-001/
```

Archive mismatch sets `archive_state=FAILED` and `purge_count=0`. Cleanup is
eligible only after verification and retains the final, contact sheet, QC and
review packet.

The human packet stays `PENDING`. An operator must watch once uninterrupted at
1.0x, score all eight dimensions, record timestamped issues and optionally use
0.75x only at flagged timestamps. Codex cannot mark this review PASS. No
YouTube write, FinalMediaRef, HumanUploadTask, UploadedVideo, learning
promotion or CH1-FLEX action is authorized.

## Current run state — 2026-07-14

Offline qualification, negative fixtures, the required regression suite and
the local golden all PASS. The golden has separate TechnicalMediaQC and
CreativePerceptualMediaQC PASS evidence, but it is synthetic and cannot stand
in for paid output or human review.

Paid preflight is `BLOCKED`: the ElevenLabs voice/model IDs and explicit
TTS/Voices/Models/Forced Alignment readiness are not confirmed. Provider probe
and call counts remain zero; every one-shot ledger attempt remains zero. Paid
media, paid QC and Drive receipt were not created. Archive and cleanup are
blocked with `purge_count=0`, and human watchability remains `PENDING`.

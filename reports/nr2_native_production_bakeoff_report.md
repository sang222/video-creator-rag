# NR2 — Native Production Bakeoff

Date: 2026-07-11. Scope: local-only, non-production.

## Verdict

```txt
NR2_TECHNICAL=PASS
NR2_STRATEGY_A=PASS
NR2_STRATEGY_B=PASS
NR2_STRATEGY_C=REVIEW_REQUIRED
NR2_HUMAN_REVIEW=PASS
NR2_SELECTED_STRATEGY=B
NR2_FINAL=PASS
PROCEED_TO_FIRST_CHANNEL_PILOT=true
```

NR1 approval evidence was already consistent: `NR1_HUMAN_REVIEW=PASS`, `NR1_FINAL=PASS`, `PROCEED_TO_NR2=true`; NR1 was not rerun or edited.

## Excerpt and common inputs

`NR2ExcerptManifest` is under `var/tmp/native_renderer/nr2/nr2-20260711-local-bakeoff/nr2_excerpt_manifest.json`. Package `d9e19d5d-dbfa-4f94-b283-92a5d919e66a`; continuous 0–84,000 ms; segments S1–S19; hook, operational problem, scenario result, mechanism setup/explanation and practical example. Source script/SRT hash: `0bdcd564...f6a2`; clipped SRT hash: `57eab8b5...126c`; excerpt hash: `3a335ed9...98d2`. Format contract `f4ef71b1...` / `8522fb38...0cbc`; originality manifest `d0bb74e3...` / `d0bf32bf...8624`.

All variants use the same deterministic 84-second synthetic AAC, 48 kHz stereo, checksum recorded in `audio_manifest.json`. Voice quality is `NOT_EVALUATED_PROVIDER_AUDIO_PENDING`.

## Strategies, plans and technical scorecard

| Strategy | Plan hash | Native / support / hero | Render / QC | Elapsed / realtime | Size | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| A explanatory | `b397b669...931f` | 85.7% / 14.3% / 0% | PASS / PASS | 15.332 s / 0.1825x | 9,614,288 B | PASS |
| B balanced | `497d27bb...5400` | 57.1% / 28.6% / 14.3% | PASS / PASS | 16.187 s / 0.1927x | 9,724,510 B | PASS |
| C hero-heavy placeholder | `cc5f6bb...8125` | 28.6% / 28.6% / 42.9% | PASS / PASS | 15.133 s / 0.1802x | 9,855,744 B | REVIEW_REQUIRED |

A uses an explicit +1 percentage-point scene discretization tolerance (6/7 native). C is technically valid but `HeroOveruseRiskGate=REVIEW_REQUIRED`; this is not a claim about Luma quality. Peak RSS and automated freeze count are unavailable and remain `null`; no values were fabricated. Caption/timeline coverage are 1.0; MediaQC reports PASS.

Outputs are the three required MP4 files under `var/tmp/native_renderer/nr2/nr2-20260711-local-bakeoff/`. Each strategy directory retains plan, compiled manifest, command manifest, filtergraph, sanitized argv shell evidence, stderr, ffprobe, MediaQC, receipt, contact sheet and output checksum. No `FinalMediaRef` exists.

`NR2PlanDiffManifest` reports only strategy key/id/hash, visual treatment, asset mapping, animation preset and projected provider intent as changed; unexpected differences: none. `SameContentBakeoffIntegrityGate=PASS`.

## Originality and projected cost

`NR2OriginalityComparisonManifest`: all three retain NO_CHARACTER, the native explanatory identity, scenario disclosure and the EpisodeOriginalityManifest sequence. A emphasizes diagrams/cards; B changes semantic asset roles while retaining a native backbone; C materially changes hero/metaphor emphasis but preserves two mechanism scenes. None uses generic stock as the explanatory backbone, fake evidence, deceptive packaging, or transition-only variation.

`NR2ProjectedCostComparison` (`PROJECTED_ONLY`, no pricing lookup): A plans 1 Pexels slot / 0 Luma / 0 hero seconds, LOW; B 2 / 1 / 12 seconds, MEDIUM; C 2 / 3 / 36 seconds, HIGH. Retry and operator burden bands are respectively LOW, MEDIUM, HIGH. These are planning bands, not real prices.

## No-execution proof and classification

No ElevenLabs, Luma, Pexels, Drive, YouTube or external network media call occurred. No production render job, provider submission, executed paid ledger, FinalMediaRef, CloudMediaRef, HumanUploadTask, provider activation, frozen-context mutation, learning promotion or prompt mutation occurred. Local placeholder provenance is explicit in `nr2_asset_substitution_manifest.json` and every asset is `production_eligible=false`.

P0: none. P1: none. P2: C hero-overuse requires human review. P3: peak RSS/freeze metrics unavailable in this minimal runner. No production pain-log mutation was required.

## Human decision

The operator explicitly selected Strategy B. It preserves the native explanatory backbone; supporting visuals improve pacing without becoming the content backbone; limited hero moments add emphasis without overuse; and projected cost, regeneration risk and operator burden are acceptable for the first channel. Strategy A remains the fallback for mechanism/data-heavy scenes. Strategy C was not selected because of `HeroOveruseRiskGate` and higher projected cost/review burden.

Next checkpoint recommendation: first-channel pilot is authorized by NR2 evidence, but was not started in this task. Provider and publishing boundaries remain unchanged.

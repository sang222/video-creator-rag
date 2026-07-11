# AS1 — Asset Acquisition, Provenance and Archive Safety

Date: 2026-07-11. Scope: local fixture only, no provider/Drive/network execution.

## Verdict

```text
AS1_ARCHITECTURE=PASS
AS1_LOCAL_FIXTURE_REHEARSAL=PASS
AS1_PROVIDER_EXECUTION=DISABLED
AS1_ARCHIVE_SAFETY=PASS
AS1_FINAL=PASS
PROCEED_TO_PA1R=true
```

PA1R was not run.

## Implemented foundation

- Script-driven `AssetRequestCompiler` bound to approved FormatIdentity, channel-scoped Strategy B, provider policy, originality/claim/disclosure evidence and NativeRenderPlan hashes.
- Native-first deterministic policy; stock factual evidence/recurring host and AI filler hard-blocked.
- Pexels English query plan, structured payload, parser, 12-dimension deterministic ranking, human-review boundary, MP4 rendition selection, redacted download reference and rate-limit parser.
- `StockSourceManifest`, `AIHeroAssetRequest`, planned-only `AIGenerationManifest`, download/checksum receipt and normalization argv manifest.
- Project workspace layout, ownership, traversal/symlink/disk/size/atomic `.part`/checksum guards.
- Complete `ProductionArchiveManifest`, fixture Drive receipt verification, strict archive-before-purge state machine and idempotent fixture cleanup.
- Three read-only evidence endpoints. No provider/render/upload/archive/purge action endpoint or dashboard button.

No migration was needed. AS1 evidence is filesystem-local and read-only; no Channel Contract, ChannelProfileVersion, EffectiveChannelRuntimeContextSnapshot, FormatIdentityContract, learning memory or prompt state was mutated.

## Fixture rehearsal

Evidence root: `var/tmp/vcos-project-workspaces/as1-small-team-ai-project/`.

The rehearsal used the approved `small-team-ai` Strategy B boundary with three native explanatory requests, one Pexels supporting request and one Luma metaphor request. Pexels response/download and Drive metadata were local fixtures only.

| Evidence | Result |
| --- | --- |
| Compiled requests | native 3 / Pexels 1 / Luma 1 |
| Pexels transport | `LOCAL_FIXTURE_ONLY` |
| Pexels provider call | false |
| Download evidence | file exists + streaming SHA-256 |
| Luma status | `PLANNED_NOT_SUBMITTED`, generation ID null |
| Normalization | sanitized argv plan, execution disabled |
| Required archive roles | complete, 17 |
| Default exclusions | rejected stock + normalized temp excluded |
| Fixture archive | `VERIFIED` |
| Drive call | false |
| Cleanup | eligible after verify; first `COMPLETED`, repeat `NOOP_IDEMPOTENT` |
| Production eligibility | false |

The archive fixture may reach `VERIFIED` only as local metadata-verifier evidence. It is not a real Drive/provider success and creates no real Drive ID.

## Safety and no-execution proof

All new execution flags default off: Pexels, Luma, ElevenLabs and global provider production execution. New AS1 services contain no HTTP/Drive client call. Secrets remain `SecretStr` Settings values and do not enter query strings, manifests, logs or archive paths. Raw tokenized rendition URLs are converted to `volatile://` hash references before durable plans.

No production render, provider submission, executed paid ledger, final/cloud media reference, human upload task, provider activation, Drive upload or YouTube action occurred.

## Verification

- Alembic heads: PASS, one head `0034_ofv0_originality`.
- `python -m compileall -q app`: PASS.
- Runtime LTS + DX2 + OFV0 + NR1 + NR2 focused regressions: PASS.
- `tests/test_as1_asset_acquisition_provenance.py`: PASS, 19 tests.
- Migration added: no; migration test not required by AS1 change.
- Frontend changed: no; frontend checks not required.
- `git diff --check`: PASS.

## Pain classification

No new P0/P1/P2/P3 finding. Production Pain Log was not changed.

# IMG-CANARY-v3 Drive export closeout

Date: 2026-07-18
Run: `img-canary-v3-20260718T162027Z-a90959ed`

The operator supplied `IMG_CANARY_V3_HUMAN_REVIEW=PASS`. The immutable receipt
records `human_review_authority=OPERATOR`; it does not attribute visual judgment
to Codex. It binds the reviewed JPEG, normalized PNG and MP4 to SHA-256 values
`3ab066bd…`, `af752598…` and `8e5a4dd…` respectively.

The original manifest remains byte-for-byte unchanged. Its internal semantic
`manifest_hash` is `45140e3e…`; its actual file-bytes SHA-256 is `0e853978…`.
Because the supplied prompt labeled the semantic hash as a file SHA, the closeout
records the discrepancy and uses a separate superseding export envelope. The 44
original roles all passed existence, size, checksum, path-boundary and secret/raw
payload exclusion checks. The provider ledger remains `SUCCEEDED/1`; closeout
Gemini calls were `0 → 0`.

Drive export used the existing OAuth/root integration and deterministic folder:

```text
smoke_tests/2026-07-18/img_canary/img-canary-v3-20260718T162027Z-a90959ed
drive_folder_id=1qqlcy3m7Ry36xFRpBJEKng94yTpYFS10
```

The export set contains 44 original roles, the original manifest, the operator
receipt and the external closeout manifest: 47 items total. The resumable journal
finished `VERIFIED`; the exact remote set has 47 unique IDs, correct parents and
names, matching sizes and compatible Drive checksums, no extras and no duplicate
names. Local and remote totals both equal 11,656,900 bytes. Receipt hash:
`c60c1f25307f21a25192dc8a2b192373996da0ee3971bb5c59803134a27046c5`.

Two deterministic repair cycles were recorded: correcting the manifest-hash
label through a non-destructive envelope, then starting the stopped local
PostgreSQL service required to read the existing OAuth reference. The latest
operator instruction explicitly skipped the automated test suite; controlled
runner validation and real Drive verification passed.

```text
IMG_CANARY_V3_HUMAN_REVIEW=PASS
IMG_CANARY_V3_DRIVE_EXPORT=PASS
IMG_CANARY_V3_DRIVE_ARCHIVE=PASS
ARCHIVE_VERIFIED=true
IMG_CANARY_V3_FINAL=PASS
PROCEED_TO_CH1_FLEX_V2=true
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
```

Next action: CH1-FLEX v2 is eligible to be planned in a separate task. It was not
started here. The canary remains non-production and not publishable; MR1 and
YouTube publication remain out of scope.

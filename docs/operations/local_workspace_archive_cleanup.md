# Local Workspace, Archive and Cleanup Operations

## Workspace

Default root: `var/tmp/vcos-project-workspaces`. Production may later set `VCOS_LOCAL_PROJECT_WORKSPACE_ROOT` to external scratch without changing contracts.

Each project owns:

```text
source/{script,audio,pexels,ai-hero}
normalized/{stock,hero,audio}
render/{scenes,proxy,final}
manifests
qc
publish
```

Never place secrets in these directories. Pexels API keys belong only to Settings/environment. Raw tokenized download URLs stay in volatile in-memory transport context and are represented durably by redacted references.

## Fixture download procedure

1. Confirm `transport=LOCAL_FIXTURE_ONLY`, `provider_call_made=false`, `production_eligible=false`.
2. Resolve the destination beneath the configured project root.
3. Reject traversal, existing symlink components, insufficient free space and oversized input.
4. Stream to a sibling `.part` file while computing SHA-256.
5. Flush/fsync and atomically rename.
6. Claim `ASSET_DOWNLOADED` only if the destination exists and its checksum matches.
7. On any failure, delete `.part` and destination output; preserve the failure evidence.

## Archive verification

The archive path is relative to the configured root and must not repeat `VCOS` or the configured-root name. Production scope requires explicit company, channel workspace and project IDs; `_unknown` segments block.

Archive state order:

```text
PLANNED -> UPLOADING -> UPLOADED_UNVERIFIED -> VERIFYING -> VERIFIED
```

The AS1 verifier consumes fake Drive metadata only. It compares every required file by expected archive path, size and SHA-256. A missing file, size mismatch or checksum mismatch produces `FAILED`.

## Cleanup gate

Cleanup requires all of the following:

- receipt state `VERIFIED`;
- no mismatch reason codes;
- local target under the project workspace;
- explicit fixture-only execution in AS1.

If any required archive file mismatches, delete zero files. Preserve `manifests/`, `qc/` and `publish/` audit evidence. A repeated cleanup call is a successful `NOOP_IDEMPOTENT`, not a second purge.

Forbidden transitions include `ARCHIVE_UPLOADING -> LOCAL_PURGED` and `ARCHIVE_UPLOADED_UNVERIFIED -> LOCAL_PURGED`. AS1 must never run cleanup against a real production workspace.

## Recovery

- Download failure: inspect the failure receipt, confirm no `.part` remains, then make a new approved attempt in a later provider phase.
- Archive mismatch: keep the complete local workspace; repair/reverify the archive. Do not partially purge.
- Cleanup deletion failure: record the path in `failed_deletions`; keep remaining files and rerun only after the receipt is still verified.
- Suspected secret exposure: stop, rotate the credential outside VCOS, remove unsafe evidence, and treat it as P0 under the post-freeze protocol.

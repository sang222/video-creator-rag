# VCOS Remaining Debt Closeout

This change closes the code-only portions of D15, P1, P2 and P3 without
claiming that the operator's local database, provider accounts, public videos,
or live multi-channel portfolio have already been validated.

## Authority order

```text
D15 series authority
→ P1 analytics and learning authority
→ P2 media-business operating state
→ P3 one-engine-many-profiles audit
```

All database changes are additive and form one Alembic chain:

```text
0084_youtube_private_delivery
→ 0085_series_authority
→ 0086_learning_authority
→ 0087_business_os
→ 0088_business_continuation
→ 0089_business_action_lifecycle
```

The migrations are executed against ephemeral PostgreSQL in CI. They are not
applied to the operator's local runtime database by this PR.

## D15 — series authority

| Debt | Closeout |
|---|---|
| SeriesArcVersion | Immutable, versioned `SeriesArcVersion` authority. |
| FIXED_COUNT vs ROLLING | Explicit `arc_mode` with database and service validation. |
| planned_episode_count | Required for fixed arcs and forbidden for rolling arcs. |
| EpisodeBlueprint / coverage | Fixed arcs cannot activate until positions `1..N` are fully covered. |
| Attempt ID vs public ordinal | `technical_attempt_ref` and `SeriesPublicOrdinal.public_ordinal` are separate identities. |
| Early completion | Append-only human lifecycle decision; moves to `COMPLETION_PENDING`, never directly to completed. |
| Extension | Human command creates a new arc version; old versions become `SUPERSEDED`. |
| Automatic completion pending | A fixed arc enters `COMPLETION_PENDING` when all planned positions are published. |
| Cross-run ordinal continuity | Unique series-level ordinal ledger allocates the next public number under row locking. |
| Dashboard progress | `SeriesProgressProjection` exposes `EP03/06` or rolling progress. |
| Playlist ordering | `playlist_position = public_ordinal - 1` is a database invariant. |

## P1 — analytics and learning

The code adds:

- immutable `AnalyticsEvidenceWindow` rows for H24/H72/D7/D30/M11;
- confidence and maturity states instead of learning from incomplete data;
- semantic `LearningEquivalenceFingerprint` values that are stable under map
  ordering;
- policy-current promotion eligibility re-checks;
- exact command and evidence uniqueness for M11/exactly-once review;
- append-only review audit history and explicit supersession cleanup;
- `LearningOperationalIncident` for NoView, policy drift, analytics drift and
  live-proof canaries;
- `AudienceDeliveryPlan` created only from a verified public-publication
  receipt.

Open policy or enforcement incidents freeze promotion. A software surface is
not treated as a successful live canary. `NO_VIEW_CANARY`, `POLICY_DRIFT`, and
`LIVE_PROOF` stay open or absent until real evidence is recorded.

## P2 — media business operating state

The code adds durable monitoring truth for:

- payment/tax/address/payment-method/hold state;
- monetization eligibility, enrollment and restrictions;
- estimated, pending, locked, finalized, reversed and paid revenue;
- channel P&L and contribution margin;
- a two-cycle `SelfFundingAssessment` that never passes on views or estimated
  revenue alone;
- generalized platform enforcement incidents and learning freeze;
- human-reviewed appeal evidence packs, with no automatic legal submission;
- immutable affiliate offer terms and canonical link registry;
- disclosure/link health assessments that fail closed;
- action-first `BusinessActionItem` records and a compact dashboard
  projection.

Sources are explicit (`API`, `OPERATOR_ATTESTATION`, or import) and carry
freshness/confidence. Missing API coverage is not fabricated.

## P3 — scale and cleanup

`ArchitectureDebtAuditService` checks production source/config for:

- remaining channel-specific `Small Team AI` branching;
- direct `if niche == ...` / `if channel_name == ...` execution forks;
- superseded `PodcastPipeline`, `ShortsPipeline`, `TopicBankItem`, or
  `PodcastNoViewAgent` production surfaces.

The portfolio proof is intentionally evidence-based: at least two channels
must have verified public publications and distinct compiled profile hashes.
Until that occurs, `Multi-channel Live Portfolio Proof` remains
`NOT_PROVEN`; the code does not manufacture a PASS.

## Publication integration

After a canonical public-publication receipt is verified, the deterministic
coordinator may create:

- a learning equivalence fingerprint;
- a proactive audience-delivery plan;
- a public series ordinal when an active D15 arc and assigned blueprint exist.

Legacy series without an active D15 arc are not silently reinterpreted. Their
publication remains valid and local migration/bootstrap work must explicitly
create the new authority.

## Boundaries retained

- YouTube private staging is not publication.
- Only verified human-public release creates canonical publication truth.
- No provider, OAuth, revenue, payment, legal, appeal or affiliate network
  call is made by these services.
- No Shorts product or niche-specific pipeline is introduced.
- No live portfolio, analytics, NoView or policy-drift proof is claimed.
- Existing channel/profile snapshots remain immutable.

## Local work after merge

The later local Codex execution must:

1. back up and inspect the production database;
2. apply `0085 → 0086 → 0087` in order;
3. bootstrap explicit D15 arcs for real series without rewriting history;
4. verify publication coordinator rows against real public receipts;
5. seed payment/monetization source attestations without secrets in DB;
6. run live analytics, NoView, policy-drift and multi-channel proof canaries;
7. report exact blockers instead of mutating authority to force PASS.

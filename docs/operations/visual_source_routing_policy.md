# Visual Source Routing Policy

## Operational status

VSR1 is an offline, read-only routing foundation. Its policy catalog and the
`small-team-ai` fixture are not bound to the active ChannelProfileVersion v1.
There is no route mutation or provider-execution endpoint.

```text
VSR1_PROVIDER_EXECUTION=DISABLED
IMG1_PROVIDER_ROUTE=google_gemini_image
IMG1_PROVIDER_EXECUTION=DISABLED
IMG1_FIXTURE_ONLY=true
MR1_EXECUTION=ON_HOLD
PROCEED_TO_MR1=false
```

## Global invariants

- minimum output is 1080p; resolution downgrade is forbidden;
- one preferred source route is required per scene;
- exact text and exact numbers are native-only authorities;
- generated media cannot be evidence authority;
- final composition belongs to `NativeFFmpegRenderer`;
- Pexels is optional, never a global scene default;
- Pexels failure cannot open AI-image or Veo execution;
- new vendors and new provider routes require operator approval;
- automated attempts are capped at one per scene when a later provider milestone
  is authorized;
- final human approval remains mandatory.

## Operator policy lifecycle

1. Review the versioned routing policy, thresholds and fixture output.
2. Preview deterministic decisions and their input/policy hashes.
3. Resolve any `UNRESOLVED_BLOCK`, low-confidence result, rights gap or missing
   authorization outside the routing service.
4. Treat `provider_execution_required=true` as a future prerequisite, not as
   permission to execute.
5. Treat the IMG1 provider foundation as fixture-only; validate IMG1, VQC1 and
   a bounded paid canary separately before proposing a new channel profile.
6. Compile, review and activate CH1-FLEX v2 only in its later approved task.
7. Revise PKG1 visual/provider/cost/disclosure artifacts and obtain new exact
   MR1 approvals before execution.

The existing unrelated `small-team-ai` draft v2 must not be activated as the
routing profile. Historical approvals and artifacts are never rewritten.

## Gate policy

The completeness gate runs first and blocks missing meaning, narrative
function, truth status, exact-text status, specificity or output requirements.
Unknown critical values never fall through to Pexels.

Initial Pexels policy is deterministic:

- eligible: filmability and stock searchability at least `0.70`; custom
  composition at most `0.30`; exact text, exact number, evidence truth and
  identity consistency at most `0.20`;
- supporting-only: filmability and searchability at least `0.50`, without a
  hard prohibition;
- prohibited: exact text/number or evidence truth at least `0.50`, custom
  composition at least `0.70`, product specificity at least `0.50`, or any
  recurring identity or named workflow requirement.

Evidence, actual UI, product and document truth require an authorized source.
Missing authorization emits `UNRESOLVED_BLOCK`. Diagram clarity at least `0.60`
selects `NATIVE_DIAGRAM`; with motion semantic value at least `0.70`, it may
select `NATIVE_MOTION_GRAPHIC`.

AI-image assessment is provider-neutral. Evidence, actual UI/product/document,
unapproved likeness and exact generated text are prohibited. Exact text on a
valid custom composition requires `AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY`.
Historical VSR1 fixtures preserve
`IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE`; IMG1 does not rewrite them. New IMG1
fixture requests may bind the registered `google_gemini_image` route, but
provider execution remains disabled.

Veo requires high semantic motion, a hero/metaphor/transition function, low
truth risk and evidence that a still or native motion treatment is insufficient.
Low-motion or diagram-clearer scenes cannot select Veo.

## Fallback policy

| Class | Allowed behavior | Prohibited behavior |
| --- | --- | --- |
| `PEXELS_ONLY` | Observable reality may remain Pexels or stop for review. | Search failure opening AI or Veo. |
| `PEXELS_PRIMARY_WITH_AI_ALLOWED` | AI must already be eligible and explicitly allowed before search; a later paid call still requires approval. | Exception-driven or automatic paid failover. |
| `AI_IMAGE_PRIMARY` | Eligible custom composition may fall back to a declared native route. | Impersonating evidence, product or UI truth. |
| `NATIVE_ONLY` | Mechanism, labels, numbers, timelines and comparisons stay native. | Generic stock or generated-art substitution. |
| `AUTHORIZED_ASSET_ONLY` | Actual UI, product, document and evidence use an authorized asset. | Treating an unverified human asset as authorized. |
| `NO_FALLBACK` | Block or escalate to a human. | Inventing an undeclared provider route. |

Allowed and forbidden route sets cannot overlap. Archive reuse may precede a
declared route only when semantic fit, rights, cooldown, originality and truth
freshness all pass; it never opens a paid fallback.

## Exact-text handling

Route previews must expose whether native overlay is required. Text-safe and
reserved-overlay rectangles use normalized coordinates and must be inside the
canvas. The overlay plan binds displayed text/numbers to authoritative script,
claim, source or UI refs and reserves caption-safe space.

Generated or stock pixels cannot own a headline, number, percentage, workflow
label, quote, citation, CTA, product/tool name, data value or actual UI text.
Missing required native-overlay content is a block, not a creative fallback.

## `small-team-ai` offline fixture

The fixture uses `niche_visual_source_profile=STOCK_ASSISTED`; it is not an
active channel policy mutation.

| Representative meaning | Expected planning route |
| --- | --- |
| approval bottleneck | `NATIVE_DIAGRAM` |
| manual or named handoff | `NATIVE_DIAGRAM` |
| context switching by a real worker | `PEXELS_VIDEO` |
| knowledge-silos metaphor with labels | `AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY` |
| automation-leverage metaphor | `AI_GENERATED_IMAGE` |
| named system flow | `NATIVE_DIAGRAM` |
| before/after workflow | `EDITORIAL_TEXT_GRAPHIC` or `NATIVE_DIAGRAM`, as fixed by the fixture input |
| hours-saved result | `EDITORIAL_TEXT_GRAPHIC` with native number authority |

The fixture also proves that an empty Pexels result does not change a previously
computed route, a named workflow cannot become office footage, and identical
inputs produce an identical decision hash.

## Read-only preview and evidence

The preview/read model may expose scene ID, niche profile, preferred route,
fallback class, confidence, reason codes, provider/approval requirements,
blockers and exact next action. It may read a route-aware artifact or an offline
fixture, but it cannot create an asset, update a route or call a provider.

For VSR1, `actual_source_route` remains null and provider URLs, responses,
attempts, actual cost events and output receipts do not exist. Route evidence
records only the feature snapshot, policy ref/hash, gate refs, fallback policy,
estimated cost class and planning decision. IMG1 readiness is a separate
configuration-only read: it can report route/model/catalog/flag state but
cannot generate content.

## Legacy and fail-closed handling

Old plans remain readable as `LEGACY_VISUAL_ROUTING`. Strict provider planning
requires one preferred route, a decision ref/hash and eligibility evidence.
An old `SUPPORTING_STOCK` or Pexels role is not proof of VSR1 eligibility.

No VSR1 operation may mutate ChannelProfileVersion, CompiledPolicySnapshot,
PKG1, MR1 approval, provider ledgers or historical artifacts. No database
migration, provider probe, render, Drive upload or YouTube action is part of
this policy.

The next authorized sequence is:

```text
VSR1 PASS -> IMG1 fixture-only foundation -> VQC1 -> offline image/overlay fixtures
-> one operator-approved paid image canary -> human review + Drive verification
-> CH1-FLEX v2 -> PKG1 visual revision -> new MR1 approval -> MR1
```

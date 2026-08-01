# M12.2 Long-Form Scripted Package

The scripted-package boundary consumes one admitted long-form VideoProject and
its frozen channel/profile/policy, niche, market, category, assignment, and
duration lineage. It does not select an idea, create an admission, or resolve an
ambiguous latest authority.

## Required input

- `ProjectAdmissionDecision=ADMIT` with schema v2;
- `production_lane=LONG_FORM`;
- `content_mode=SERIES_EPISODE | STANDALONE`;
- exact ChannelProfileVersion and CompiledChannelPolicySnapshot;
- strict IdeaMarketPreflight niche/market digests;
- approved research evidence and effective channel context;
- the channel-owned duration contract.

The package fingerprint binds admission/project hashes, profile input hash,
compiled-policy hash, niche/market digest hashes, editorial slot, research,
assignment, destination, and package-builder version. The same fingerprint
returns the existing immutable package. Changed lineage requires a new version.

## Safety boundary

Package construction is automated and is not a human decision point. A package
does not imply provider execution, render completion, archive verification, or
upload permission. Automated readiness must pass before the durable production
workflow can continue. The sole normal human boundary remains the exact final
rendered video decision: `UPLOAD | DO_NOT_UPLOAD`.

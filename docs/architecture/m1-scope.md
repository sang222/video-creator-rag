# M1 Scope

## Included

- Company service.
- ChannelWorkspace and channel membership persistence.
- NicheProfileTemplate catalog as channel initialization input.
- CapabilityMatrix and profile compiler policy catalogs.
- ChannelProfileVersion as channel-level profile truth.
- Deterministic ChannelProfileCompiler skeleton with no LLM calls.
- CompiledChannelPolicySnapshot as immutable runtime policy snapshot.
- Profile compile run ledger.
- Minimal approval and activation state.
- Minimal API and CLI smoke workflow.

## Excluded

- VideoProject.
- Artifact workflow.
- Daily scheduler.
- Agent runtime.
- Media pipeline.
- Publish or upload pipeline.
- Analytics.
- Dashboard UI.
- Queue broker.
- Provider integrations.

## Truth Model

NicheProfileTemplate is not runtime truth. It is a config template used to initialize a channel profile.

ChannelProfileVersion is the channel-level profile truth.

CompiledChannelPolicySnapshot is the immutable runtime policy snapshot.

Future VideoProject records must reference an explicit snapshot id. They must not lookup the latest profile or latest snapshot during project execution.

M1 does not implement media pipeline. CapCut pilot findings do not make CapCut a production dependency.

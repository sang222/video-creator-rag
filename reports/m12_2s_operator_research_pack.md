# M12.2S Operator Research Pack

Topic: How small teams can use an AI video operating system without accidentally calling paid media providers.

Source notes:

- Local VCOS reports state that Prompt Registry, Channel Contract, and prompt audit snapshots are available.
- Local VCOS reports state that mock and dry-run production success paths were purged.
- Local VCOS reports state that the first scripted video package path exists and stops before media generation.
- Local VCOS reports state that upload/publish is manual handoff only; VCOS must not upload or publish automatically.
- Local channel contract for Small Team AI targets practical AI workflow explainers for small teams.
- Local provider readiness shows ElevenLabs and Creatomate are not fully configured for paid media generation.

Facts:

- The rehearsal should use real Ollama through LLMRouter.
- Prompt rendering should include system and user messages.
- Text agents may create script, metadata, visual plan, thumbnail brief, rights review, gatekeeper review, upload card, provider readiness summary, and media QC explanation.
- Visual and thumbnail agents must create plans or briefs only.
- Media QC cannot pass before a media file exists.
- The expected boundary is blocked until voice and render providers are configured.

Assumptions:

- This run is a production-style rehearsal, not a publish workflow.
- The operator wants a safe package for human review before buying or enabling media providers.
- Google Vertex Veo is optional for hero shots.

Open questions:

- Which exact ElevenLabs voice should be selected after provider onboarding?
- Which Creatomate template should be used after renderer onboarding?
- Whether a Veo hero shot is worth the cost for this video.

Conflicts:

- None identified in local source notes.

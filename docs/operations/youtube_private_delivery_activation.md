# YouTube Private Delivery — Live Activation

Code and CI do not activate live delivery. The active Card C lane is:

`QC PASS -> automatic YouTube PRIVATE staging -> PRIVATE_VERIFIED -> human Studio PUBLIC -> read-only PUBLICATION_VERIFIED`.

Activation requires:

1. A channel-bound YouTube publishing credential with only the approved private-upload capabilities.
2. A Telegram bot/chat binding if delivery notification is enabled.
3. A QC-passed candidate with exact local media checksum, thumbnail binding, frozen metadata, and valid local caption sidecar.
4. Automatic candidate-bound private-stage enqueue and worker execution.
5. Read-back proof that privacy remains `PRIVATE`, processing succeeded, metadata/components match, and no public-release API action occurred.
6. Local purge only after immutable private-stage verification.
7. Human review of the actual staged package in YouTube Studio and manual PUBLIC release.
8. Read-only observation of the exact staged video becoming PUBLIC before `PublicPublicationReceipt`, `UploadedVideo`, analytics, series, or learning effects.

Human publication remains outside VCOS. Legacy `FinalVideoDecision`/manual-confirmation records remain readable, but they are not required by the active candidate-bound success path.

## Local activation authority

The historical local canary procedure is retained in `youtube_private_delivery_local_codex_master_prompt.md`; it must not be used as the active Card C success-path contract. The prompt is intentionally evidence-first, forbids public-release API calls, and stops at the human boundary before canonical publication verification.

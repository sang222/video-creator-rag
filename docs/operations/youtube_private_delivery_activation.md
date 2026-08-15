# YouTube Private Delivery — Live Activation

Code and CI do not activate live delivery. Local production activation requires:

1. A channel-bound YouTube publishing credential with only the approved private-upload capabilities.
2. A Telegram bot/chat binding if delivery notification is enabled.
3. A real `FINAL_REVIEW_READY` package, exact media checksum, thumbnail binding, frozen metadata, and valid caption sidecar.
4. Operator-controlled execution of the private staging command.
5. Read-back proof that privacy remains `PRIVATE`, processing succeeded, metadata/components match, and no public-release API action occurred.
6. Local purge only after immutable private-stage verification.

Human publication remains outside the automated staging command. After the operator publishes in YouTube Studio, VCOS must observe and verify the public state before creating `PublicPublicationReceipt` and opening analytics/series/learning downstream effects.

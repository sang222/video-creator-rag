# YouTube Private Delivery Invariants

- YouTube private staging is a storage/review effect, not publication.
- Private staging cannot create `UploadedVideo`, analytics schedules, series publications, or learning evidence.
- Every external write has a durable identity before submission.
- Ambiguous provider outcomes are reconciled or blocked; they are never blindly repeated.
- Title, description, tags, disclosure, thumbnail, caption, channel binding, and final-media checksum are frozen before upload.
- Only `privacyStatus=PRIVATE` is permitted in the automated staging lane.
- Public release, `publishAt`, delete, unpublish, and browser automation are forbidden.
- Local final MP4 purge requires checksum-verified private-stage read-back.
- Telegram notification is an outbox effect and never changes delivery/publication authority.
- Series playlist binding is explicit and separate from publication; standalone content is not inferred into a series.

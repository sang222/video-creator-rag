# YouTube Private Delivery Failure Modes

| Failure | Required disposition |
|---|---|
| Credential or capability missing | Block before any provider call |
| Combined media/metadata lineage drift | Block before session creation |
| Upload outcome uncertain | Reconcile the exact resumable session; never create a second video |
| Thumbnail/caption outcome uncertain | Reconcile the exact component attempt; never blindly resend |
| Processing failed or platform rejected | Keep unpublished, record platform-ingestion failure, do not advance analytics or series |
| Public release not observed | Remain `WAITING_HUMAN_RELEASE` |
| Telegram outcome uncertain | Do not resend automatically |
| Local purge preconditions incomplete | Preserve local bytes |
| Playlist binding drift | Block playlist settlement without re-uploading the video |

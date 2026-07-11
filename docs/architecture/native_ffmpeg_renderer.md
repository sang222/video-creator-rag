# NativeFFmpegRenderer architecture

NR1 freezes the first-channel render path as `NativeRenderPlan → NativeMotionCompiler → CompiledNativeRenderManifest → FFmpegCommandManifest → NativeFFmpegRenderer → MediaQCReport → HumanReviewReceipt`.

The renderer is a local capability, not an external or paid provider. It accepts compiled manifests only, executes argv without a shell, writes `.part.mp4`, atomically renames on success, and creates no provider ledger, upload task, Drive state, YouTube state, CloudMediaRef, or production FinalMediaRef. Production execution defaults disabled. The renderer cannot infer narrative treatment, select assets, or accept raw filter syntax.

Active output profiles are 1080p30 landscape and portrait H.264 VideoToolbox, yuv420p/BT.709, AAC 48 kHz stereo and MP4 fast-start. VideoToolbox is recorded as target/max VBR; strict CBR is not claimed. 4K and ProRes remain inactive.

FormatIdentityContract and EpisodeOriginalityManifest refs/hashes are copied into the immutable plan and compiled evidence. The renderer cannot mutate them.

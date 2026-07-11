# Native renderer local workspace

Default root: `<repo>/var/tmp/native_renderer/`; override with `VCOS_NATIVE_RENDER_WORKSPACE_ROOT`.

`VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED=false` and `VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED=false` are safe defaults. NR1 test tooling may explicitly enable only `NR1_LOCAL_SYNTHETIC_SMOKE` with `production_eligible=false`.

All inputs, generated filtergraphs and outputs must resolve inside the root. `..`, outside-root paths and symlink inputs are rejected. A process lock permits one render at a time. Rendering aborts below the configured safety reserve, uses partial output plus atomic rename, and emits a cleanup receipt. Keep binary media in this ignored workspace, never in git.

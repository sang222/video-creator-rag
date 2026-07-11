# NR2 local native bakeoff

Run only from repo root:

```bash
PYTHONPATH=. .venv/bin/python tools/native_ffmpeg/nr2/run_nr2.py
```

The runner is local-only and non-production. It uses the approved 0–84 second rehearsed SRT excerpt, one deterministic synthetic AAC fixture, three immutable plan variants, ffmpeg-full, VideoToolbox H.264, burned captions, sequential execution, MediaQC, receipts and contact sheets. It requires at least 40 GiB free before every render and confines output to `var/tmp/native_renderer/nr2/`.

Provider names in substitution manifests are future intent only. No provider, Drive, YouTube, network media, production entity or dashboard execution endpoint is used. Human selection is mandatory after technical completion.

# PA1R Provider Smoke — Human Review

```text
state=PENDING
run_id=pa1r-20260713-guarded-smoke-004
final_smoke_mp4=NOT_CREATED_VEO_FAILED
contact_sheet=NOT_CREATED_VEO_FAILED
media_qc=NOT_RUN
drive_archive=NOT_RUN
publishable=false
```

Pexels search/download PASS với asset `32150707` của `Usman AbdulrasheedGambo`. ElevenLabs narration PASS structural QC, duration `24.102313s`, voice `Adam - Dominant, Firm`, model `eleven_multilingual_v2`.

Veo `veo-3.1-fast-generate-preview`, prompt hash `279fcaf252346670d72a78b18d0007a69a0e6df6f7881bc3208991ae10fd11d6`, hero intent `METAPHOR`, nhưng Gemini API reject `personGeneration=dont_allow` bằng HTTP 400. Không có operation/output để review character policy, provider audio, render hoặc archive. Codex không đánh dấu human review PASS.

- [ ] narration is understandable and complete
- [ ] Pexels footage is relevant and not misleading
- [ ] Veo hero clip adds real visual value
- [ ] no character/human-likeness conflict
- [ ] provider audio did not leak into final mix
- [ ] native/support/hero balance is acceptable
- [ ] captions are readable
- [ ] no black flash or corruption
- [ ] audio/video sync is acceptable
- [ ] provenance is understandable
- [ ] Drive archive package is complete
- [ ] output is visibly non-production

No-publish: không có YouTube call, FinalMediaRef, HumanUploadTask hoặc UploadedVideo.

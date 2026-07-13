# PA1R Provider Smoke — Human Review

```text
state=PASS
approved_at=2026-07-13T23:21:15+07:00
run_id=pa1r-20260713-guarded-smoke-005
final_mp4=/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-20260713-guarded-smoke-005/render/final/pa1r-provider-smoke.mp4
review_proxy=/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-20260713-guarded-smoke-005/render/proxy/pa1r-review-proxy.mp4
contact_sheet=/Users/sangss/Desktop/video-creator-rag/var/tmp/vcos-project-workspaces/pa1r-20260713-guarded-smoke-005/render/proxy/pa1r-contact-sheet.jpg
media_qc=PASS
drive_archive=VERIFIED
publishable=false
```

Provenance:

- Pexels reused từ run `-004`: asset `32150707`, file `13707650`, creator `Usman AbdulrasheedGambo`.
- ElevenLabs reused từ run `-004`: `Adam - Dominant, Firm`, voice `pNInz6obpgDQGcFmaJgB`, model `eleven_multilingual_v2`.
- Veo: `veo-3.1-fast-generate-preview`, prompt hash `279fcaf252346670d72a78b18d0007a69a0e6df6f7881bc3208991ae10fd11d6`.
- Transport: `person_generation=allow_all`, `generate_audio` omitted; domain policy `NO_CHARACTER` và prompt/negative prompt cấm people/faces/presenter/human likeness.
- Veo provider audio: present, một AAC stereo 48 kHz stream; discarded bằng `-an`; normalized stream count `0`; final narration source `ELEVENLABS`.
- Cost: estimate `0.80 USD`; actual billed amount unavailable từ provider operation.
- Drive: folder `1NwH5-lwkESp3-ZLDscmusxm4wjK5b5lr`, 37/37 files verified.

- [x] narration understandable
- [x] pronunciation acceptable
- [x] Pexels footage relevant
- [x] Veo clip adds visual value
- [x] no people/faces/presenter appeared in the Veo scene
- [x] NO_CHARACTER policy respected
- [x] provider audio did not leak
- [x] visual balance acceptable
- [x] captions readable
- [x] no black flash/corruption
- [x] A/V sync acceptable
- [x] provenance understandable
- [x] Drive archive complete
- [x] visibly non-production

Operator đã xem MP4 và explicit xác nhận `PA1R_HUMAN_REVIEW=PASS`. No-publish vẫn giữ nguyên: không YouTube write, FinalMediaRef, HumanUploadTask hoặc UploadedVideo.

# NR0-LITE Human Review Checklist

Status: `NR0_LITE_HUMAN_REVIEW=PASS`

Operator reviewed all listed local artifacts, including `nr0l_1080p_end_to_end.mp4`; result: PASS.

| Artifact | Video | Contact sheet | QC |
| --- | --- | --- |
| Drawtext probe | `var/tmp/native_ffmpeg_nr0_lite/outputs/nr0l_drawtext_probe.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_drawtext_probe.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_drawtext_probe/MediaQCReport.json` |
| Subtitle probe | `var/tmp/native_ffmpeg_nr0_lite/outputs/nr0l_subtitle_probe.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_subtitle_probe.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_subtitle_probe/MediaQCReport.json` |
| Slide left | `var/tmp/native_ffmpeg_nr0_lite/outputs/NR0L_slideleft.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_slideleft.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_slideleft/MediaQCReport.json` |
| Fade dissolve | `var/tmp/native_ffmpeg_nr0_lite/outputs/NR0L_fade_dissolve.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_fade_dissolve.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_fade_dissolve/MediaQCReport.json` |
| Lower third | `var/tmp/native_ffmpeg_nr0_lite/outputs/NR0L_lowerthird.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_lowerthird.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_lowerthird/MediaQCReport.json` |
| Ken Burns | `var/tmp/native_ffmpeg_nr0_lite/outputs/NR0L_kenburns.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_kenburns.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_kenburns/MediaQCReport.json` |
| Caption | `var/tmp/native_ffmpeg_nr0_lite/outputs/NR0L_caption.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_caption.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_caption/MediaQCReport.json` |
| Logo bug | `var/tmp/native_ffmpeg_nr0_lite/outputs/NR0L_logo_bug.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_logo_bug.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_logo_bug/MediaQCReport.json` |
| End-to-end | `var/tmp/native_ffmpeg_nr0_lite/outputs/nr0l_1080p_end_to_end.mp4` | `var/tmp/native_ffmpeg_nr0_lite/contact_sheets/NR0L_1080P_END_TO_END.jpg` | `var/tmp/native_ffmpeg_nr0_lite/runs/NR0L_1080P_END_TO_END/MediaQCReport.json` |

- [x] drawtext renders correctly
- [x] subtitle font/glyphs render correctly
- [x] slide transition is smooth
- [x] fade/dissolve is smooth
- [x] lower-third is readable and not cropped
- [x] Ken Burns is smooth and not jittery
- [x] captions are readable and in safe area
- [x] logo bug is correctly positioned
- [x] end-to-end video has no black flash
- [x] no obvious dropped frame
- [x] no obvious color corruption
- [x] no audio desync
- [x] audio is present and usable
- [x] output is acceptable as first-channel 1080p render foundation

The end-to-end `freezedetect` warnings were reviewed against intended low-motion/still segments and accepted. No unintended visual freeze was observed.

Operator decision:

```txt
NR0_LITE_HUMAN_REVIEW=PASS
NR0_LITE_FINAL=PASS
PROCEED_TO_OFV0_NR1=true
```

This checklist does not run OFV0/NR1.

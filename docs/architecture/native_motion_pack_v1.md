# NativeMotionPack_v1

> CQR1 extension: motion remains subordinate to narration-derived scene spans.
> The compiler receives canonical caption cues and approved visual bindings;
> it may not reconstruct cue timing, alter scene duration to fit an eight-second
> Veo output, or alternate providers to satisfy a ratio. Caption geometry and
> visual continuity gates execute before render compilation.

The registry in `app/services/native_motion_compiler.py` stores typed metadata and compiler-handler names, never executable shell fragments.

- Transitions: cut, fade_soft, fade_black, dissolve, slide_left/right, cover_left, reveal_up.
- Still/native: hold_static, kenburns_center_soft, kenburns_subject_left, pushin_slow, pan_left/right_slow.
- Cards/UI: lowerthird_slidein, fact_card_pop, data_card_hold, comparison_reveal, timeline_step_reveal, cta_card_fadeup.
- Overlay: caption_burn_ass_v1, logo_bug_static, badge_corner.
- Audio: voice_only_basic, voice_music_duck_basic, fade_in_out_basic.

Defaults and clamps are deterministic. Unsupported semantics or filter/control characters block compilation.

For `CANONICAL_STRICT`, transition duration may be derived from a canonical scene duration, but motion compilation cannot change scene, caption, asset-in/out or audio timing. The compiler first verifies the `CanonicalMediaTimeline` ref/hash, final audio ref/endpoint, caption render hash/style and exact scene anchors; missing canonical cues never fall back to SRT. Missing or conflicting authority blocks before motion presets are resolved.

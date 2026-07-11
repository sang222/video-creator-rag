# PA1-PRECHECK-SRT Ollama Rehearsal Report

Date: 2026-07-09

## Scope

- Channel key: `small-team-ai`
- Topic: `How One Automation Can Save a Small Team 20 Hours Every Week`
- Runtime: real Ollama text path through `LLMRouter`
- Mode: text artifacts only, manual publish only
- Explicitly not executed: ElevenLabs, Luma, Creatomate, Pexels, Google Drive upload, YouTube upload/publish, audio/video/media generation

## Primary Run

- Package id: `7c4a6b4e-60d2-4e99-8d65-304afef33c2b`
- Video project id: `372a1e94-3d3a-45e0-bab0-55f1916bb662`
- Package status: `WAITING_PROVIDER_CONFIG`
- Script word count: `1147`
- Estimated narration seconds: `491.571`
- Frozen WPM: `140`
- Target range: `405-495s`

## Agent Sequence

1. `ChannelAuthorityAgent`
2. `TopicIdeaScoringAgent`
3. `ResearchPackSummarizer`
4. `ScriptPlanningAgent`
5. `ScriptWriterAgent`
6. `PublishingMetadataAgent`
7. `VisualPlanningAgent`
8. `ThumbnailBriefAgent`
9. `RightsDisclosureReviewer`
10. `GatekeeperSoftReviewAgent`
11. `ScriptRewriteAgent` (`SKIPPED_SAFE`)
12. `UploadCardCopyAgent`
13. `ProviderReadinessSummaryAgent`
14. `MediaQCExplanationAgent`
15. `SRTCaptionArtifactGenerator`

## SRT Artifact

- Local path: `/Users/sangss/Desktop/video-creator-rag/var/tmp/pa1-precheck-srt/7c4a6b4e-60d2-4e99-8d65-304afef33c2b/narration.en.srt`
- Artifact type: `SRT_CAPTION_FILE`
- Language: `en`
- Caption count: `107`
- SRT total duration: `491.571s`
- Checksum SHA256: `0bdcd564a3d47c342b52bc0d057a510656e380087fbcd69d0ab4015ea310f6a2`
- Lifecycle flags: `not_final_media=true`, `not_publishable=true`, `provider_calls_made=false`, `upload_publish_made=false`
- Cloud refs: no `CloudMediaRef`, no `FinalMediaRef`

First caption:

```srt
1
00:00:00,000 --> 00:00:05,143
Every week, your team loses hours on
repetitive coordination tasks that could
```

Last caption:

```srt
107
00:08:09,002 --> 00:08:11,571
automation that makes a real difference.
```

## SRT Gates

| Gate | Status | Key measurements |
| --- | --- | --- |
| `SRTFormatGate` | PASS | `cue_count=107`, `starts_at_zero=true`, valid local SRT path |
| `SRTTimingGate` | PASS | `srt_total_seconds=491.571`, `narration_total_seconds=491.571`, `large_gap_count=0`, range `405-495s` |
| `CaptionCoverageGate` | PASS | `coverage_percent=100.0`, `script_sentence_count=55`, `covered_sentence_count=55`, no missing sentence ids |
| `CaptionReadabilityGate` | PASS | no invalid durations, no overlong lines, no too-dense captions |
| `ScriptToSRTConsistencyGate` | PASS | `script_word_count=1147`, `srt_word_count=1147`, `token_delta=0` |
| `HookCaptionGate` | PASS | first 3s caption supports opening hook, no overpromise terms |

## Hook 3s Caption Check

- First 3s caption text: `Every week, your team loses hours on repetitive coordination tasks that could`
- Hook overlap terms include: `every`, `week`, `team`, `hours`, `coordination`, `tasks`
- Derived from opening narration: `true`
- Overpromise matches: none

## Visual/SRT Alignment

| Check | Status | Evidence |
| --- | --- | --- |
| Visual scene coverage | PASS | `scene_count=18`, `timing_mode=sentence_id_derived` |
| Caption coverage by visual plan | PASS | `missing_caption_sentence_ids=[]`, `caption_count=107` |
| Timeline bounds | PASS | `srt_total_seconds=491.571`, no scene timings exceed narration/SRT end |
| Scene explosion check | PASS | `scene_count_with_timing=0`; sentence-derived grouped scenes, not one-caption-per-scene |

## Provider Dry Validation

- Dry validation status: `REACHED`
- `will_execute=false`
- `no_network_call_made=true`
- Observed provider keys only as readiness/planning refs: `creatomate_growth_10k`, `elevenlabs`, `luma_api`
- Boundary status: `BLOCKED_PROVIDER_NOT_CONFIGURED`
- Boundary reasons: `ELEVENLABS_NOT_CONFIGURED`, `CREATOMATE_GROWTH_10K_NOT_CONFIGURED`
- This is expected before purchasing/activating provider credentials.

## No-Execution Proof

| Artifact / ledger | Count |
| --- | ---: |
| `RenderRevision` for package | 0 |
| `FinalMediaRef` for project | 0 |
| `CloudMediaRef` for project | 0 |
| `HumanUploadTask` for package | 0 |
| `MediaRenderJob` for project | 0 |
| `ProviderJobSnapshot SUBMITTED` for package revisions | 0 |
| `ProviderJobSnapshot total` for package revisions | 0 |
| `PaidProviderCallLedger EXECUTED` for package revisions | 0 |
| `PaidProviderCallLedger total` for package revisions | 0 |
| `UploadedVideo` for project/package | 0 |
| Paid media/upload `ProviderAttempt` for package | 0 |
| `VideoGenerationBoundary` for package | 1, `no_provider_calls_confirmed=true` |

## Stability Rerun

| Run | Package id | Status | Words | SRT seconds | Captions | Checksum |
| --- | --- | --- | ---: | ---: | ---: | --- |
| Primary | `7c4a6b4e-60d2-4e99-8d65-304afef33c2b` | `WAITING_PROVIDER_CONFIG` | 1147 | 491.571 | 107 | `0bdcd564a3d47c342b52bc0d057a510656e380087fbcd69d0ab4015ea310f6a2` |
| Stability | `a43867b8-bdfd-45f2-ab9b-a0ee0e70cc06` | `WAITING_PROVIDER_CONFIG` | 1109 | 475.286 | 110 | `444b8408479b03fc3928e914c3799f1cb580260ed159e08950b2c4af1fef19dd` |

## Verification Commands

- `PYTHONPATH=. .venv/bin/alembic heads`: PASS, head `0033_p1_pre_lts_disposition`
- `PYTHONPATH=. .venv/bin/python -m compileall -q app`: PASS
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d10_runtime_lts_freeze.py -q`: PASS, `13 passed`
- `PYTHONPATH=. .venv/bin/pytest tests/test_dx2_provider_stack_reconciliation.py -q`: PASS, `7 passed`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_runtime_dashboard_ops.py -q`: PASS, `2 passed`
- `PYTHONPATH=. .venv/bin/pytest tests/test_r3d9_ux2_packaging_review_queue.py -q`: PASS, `24 passed`
- `PYTHONPATH=. .venv/bin/pytest tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_r3d4_agent_output_contract_gates.py tests/test_m1_channel_aware_packaging_handoff.py -q`: PASS, `130 passed`
- Relevant SRT/caption tests: included in `test_m12_2s_full_agent_ollama_rehearsal.py` and `test_r3d4_agent_output_contract_gates.py`

## Verdict

- `PA1_PRECHECK_SRT=PASS`
- `OK_TO_BUY_PROVIDER_CREDENTIALS=true`

Provider purchase is allowed from this precheck standpoint only. Provider activation/execution remains blocked until credentials are configured and a separate guarded activation step is explicitly approved.

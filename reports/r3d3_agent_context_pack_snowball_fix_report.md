# R3D3 - AgentContextPack Digests by Agent Role / Snowball Fix

## Kết quả

Đã thay raw/full context injection trong M12.2/M12.2S bằng AgentContextPack theo agent role. Prompt production chỉ nhận digest/ref/hash/slice đã allowlist; full data vẫn nằm trong DB/snapshot để audit/replay.

## Files changed

- `app/services/r3d3.py`: AgentContextContract, AgentContextPackBuilder, digest builders, budget gate, shape gate.
- `app/db/models/r3d3.py`: `AgentContextPackSnapshot`.
- `app/contracts/r3d3.py`: read contract cho snapshot.
- `alembic/versions/0026_r3d3_agent_context_pack.py`: migration bảng snapshot.
- `app/services/m12_1.py`, `app/prompts/agents/user_templates/base_task_payload.md`: render prompt bằng context pack/ref, không render full channel/policy JSON.
- `app/services/m12_2.py`: M12.2/M12.2S build/link AgentContextPack trước LLM call.
- `app/db/models/__init__.py`, `app/contracts/__init__.py`, `app/services/__init__.py`: exports.
- Tests: `tests/qualification/test_r3d3_agent_context_pack.py`, M12.1/M12.2/M12.2S qualification tests, migration fixtures.

## Models/contracts added

- `AgentContextPackSnapshot`: lưu package/project, agent/lane, contract hash, effective context snapshot/hash, channel/policy hash, context_pack_hash, prompt_context_hash, digest refs, budget report, omitted report, shape gate result.
- `AgentContextContract`: per-agent allowlist/denylist, lane, budget, `max_memory_facets=0`, `raw_artifact_allowed=false`, `full_debug_allowed=false`, `content_hash`.
- `AgentContextPackSnapshotRead`: API/read model tối thiểu.

## AgentContextContract

Registry typed Python theo `agent_key`. Builder resolve contract, đọc `EffectiveChannelRuntimeContextSnapshot` frozen từ R3D2, build candidate digest, chỉ chọn section trong allowlist, reject section forbidden, không silently drop required context. Nếu required context không fit sau compact optional, snapshot vẫn persist và trả `CONTEXT_BUDGET_EXCEEDED`.

## Per-agent allowlist summary

- ScriptWriter: script contract, effective runtime digest, script plan digest, evidence, duration policy, runtime guard, common skill digest.
- VisualPlanning: visual contract, script sentence/timeline digest, visual source policy, optional asset inventory.
- ThumbnailBrief: thumbnail contract, title/hook, visual style, optional character thumbnail.
- PublishingMetadata: metadata contract, script digest, evidence/disclosure, title style/locale.
- RightsDisclosureReviewer: source rights/disclosure, metadata, visual plan, provider/media state.
- UploadCardCopy: publish handoff, metadata, disclosure, CTA flags.
- ProviderReadinessSummary: runtime guard, provider readiness, package status.
- MediaQCExplanation: package summary, provider readiness, media inventory, gate summary.

## Prompt budget policy

Lane budgets: `cheap_structured=12000`, `long_context_text=16000`, `visual_creative_review=12000`, `gatekeeper_soft_review=18000`, `engineering_architect=12000`; MediaQC có budget riêng `14000`. Gate remove optional context trước, compact qua digest/ref/hash, sau đó BLOCK nếu required vẫn vượt budget. Metrics persisted: prompt chars, estimated tokens, context/digest chars, omitted count, largest contributors.

## Audit/replay

Full channel contract, compiled policy snapshot, effective runtime snapshot và full artifacts không bị xóa. Prompt chỉ nhận digest/ref/hash; `PromptRenderRun` vẫn giữ channel/policy JSON cho audit hiện hữu. `AgentContextPackSnapshot` link vào `PromptRenderRun` sau render và giữ `context_pack_hash`, `prompt_context_hash`, digest refs, omitted report, full-debug replay metadata.

## M12.2/M12.2S prompt path

M12.2/M12.2S pre-generate `package_id`, build AgentContextPack trước render, chặn nếu missing effective context hoặc shape/budget invalid. `previous_artifacts`, full channel contract JSON, full compiled policy JSON, provider readiness raw không còn đi vào prompt production. M12.1 render sanitize contract object: prompt nhận contract ref/hash, snapshot vẫn giữ full contract.

## Tests run

- `PYTHONPATH=. .venv/bin/python -m compileall app` - pass.
- `PYTHONPATH=. .venv/bin/alembic heads` - `0026_r3d3_agent_context_pack (head)`.
- `PYTHONPATH=. .venv/bin/pytest -q tests/qualification/test_r3d3_agent_context_pack.py tests/qualification/test_m12_1_prompt_registry.py tests/qualification/test_m12_2_first_scripted_video_package.py tests/qualification/test_m12_2s_full_agent_ollama_rehearsal.py` - 32 passed.
- `PYTHONPATH=. .venv/bin/pytest -q tests/test_r3d1_hierarchical_scope.py tests/test_r3d2_effective_channel_runtime_context.py tests/test_migration.py` - 27 passed.
- `git diff --check` - pass.

## Follow-up R3D4

Đề xuất R3D4 xử lý Agent output contract validation sâu hơn, compact replay tooling, và policy review cho per-agent schema repair. Chưa làm deterministic QC gates, Controlled Memory, vector retrieval, dashboard UI, provider/media calls, hay section-level targeted repair loops.

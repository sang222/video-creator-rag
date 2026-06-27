# M12.1 Prompt Registry

M12.1 adds a deterministic prompt contract layer for production VCOS agents.

## Decisions

- Canonical prompts live in `app/prompts/`.
- Database rows are audit/run-state/replay records, not the only canonical prompt source.
- LLMRouter chooses the lane/model.
- Prompt Registry chooses allowed role behavior and output contract.
- Channel Contract chooses the channel boundary and must be frozen per render.

## Repo Source

- Registry manifest: `app/prompts/registry/agents.yaml`
- Common skills: `app/prompts/common/*.md`
- Agent deltas: `app/prompts/agents/system_deltas/*.md`
- User template: `app/prompts/agents/user_templates/base_task_payload.md`
- Output schema: `app/prompts/schemas/base_envelope.schema.json`
- Eval fixtures: `app/prompts/fixtures/eval_cases/*.json`

Every required agent has:

- `agent_key`
- `template_key`
- `template_version`
- `system_delta_ref`
- `user_template_ref`
- `output_schema_ref`
- `allowed_router_lanes`
- `default_router_lane`
- `input_contract`
- `output_contract`
- `safety_policy_refs`
- `common_skill_refs`
- `channel_contract_required`
- `market_locale_context_required`

## DB Audit

M12.1 adds:

- `prompt_template_records`
- `agent_prompt_profiles`
- `prompt_contract_versions`
- `structured_output_schemas`
- `prompt_render_runs`
- `prompt_audit_snapshots`
- `prompt_evaluation_cases`
- `prompt_evaluation_runs`

`prompt_render_runs` binds rendered messages to:

- `channel_contract_json`
- `compiled_policy_snapshot_json`
- `channel_profile_version_id`
- `compiled_policy_snapshot_id`
- `prompt_hash`
- `prompt_context_hash`
- `router_lane`

## Channel Contract

Production prompt rendering must receive a frozen ChannelProfileVersion and CompiledChannelPolicySnapshot, plus canonical channel contract JSON.

If required channel contract data is missing, incomplete, stale, or contradictory, render returns `REVIEW_REQUIRED` with:

`Bổ sung hoặc compile lại ChannelProfileVersion trước khi render prompt.`

No agent may infer US/English/USD/timezone defaults, mutate ChannelProfileVersion, or suggest config upgrades unless the human explicitly asks for configuration advice.

## Ollama Messages

Production prompt runs use chat messages:

```json
[
  {"role": "system", "content": "common skills + agent delta + output contract"},
  {"role": "user", "content": "task payload + frozen channel contract refs"}
]
```

Legacy raw prompt callers remain supported by a shim.

## Validation

VCOS validates model JSON output itself.

Safe repair is syntax/shape only:

- strip JSON code fences
- trim to a JSON object
- do not add claims, evidence, metrics, or rights
- do not change decision semantics
- never turn `BLOCK` into `PASS`

## Activation

1. Edit prompt files in `app/prompts/`.
2. Keep `template_version` stable for copy-only fixes that do not change the contract.
3. Bump `template_version` for behavioral, schema, safety, or output changes.
4. Run `POST /prompt-registry/sync` or `PromptRegistryService.sync_repo_registry()`.
5. Run prompt evaluation cases.
6. Review `prompt_hash` changes before using the new template in production.

## Rollback

1. Restore the prior prompt file(s) or mark the new manifest entry `DEPRECATED`.
2. Restore the previous `ACTIVE` manifest entry.
3. Run prompt registry sync again.
4. Confirm the previous `prompt_hash` is active.
5. Existing `prompt_render_runs` and `prompt_audit_snapshots` remain immutable replay records.

## Non-Goals

M12.1 does not add real provider calls, provider strategy changes, YouTube upload, auto publish/upload/reupload, channel config mutation, dashboard scraping, browser automation, fake engagement, platform evasion, or TikTok/Facebook learning loops.

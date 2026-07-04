# Domain Glossary

## Channel Contract Runtime Authority
Quyền quyết định scope runtime: company, channel, category, character, voice, policy snapshot. Không agent nào được mutate ChannelProfileVersion.

## EffectiveChannelRuntimeContextSnapshot
Snapshot bất biến của channel contract + category + character/voice refs + policy context cho một VideoProject.

## AgentContextPack
Gói context đã scope cho từng agent. Chỉ chứa digest/ref an toàn; không inject raw memory, raw vector row, hoặc script cũ đầy đủ.

## Output Contract
Schema và shape output mà agent phải trả về. Sai shape đi qua strict repair/gate, không tự sửa prompt.

## Packaging Handoff
Read-only handoff cho người vận hành: title/description/thumbnail/subtitle/checklist/rights refs, không upload tự động.

## Controlled Memory
Memory item/facet có scope, rights, prompt safety, freshness và human approval. Không auto-promote từ learning thô.

## Vector-Safe Retrieval
Retrieval SQL-filter-first theo scope/context, sau đó mới rank/digest. Agent nhận digest/ref, không query vector DB trực tiếp.

## Closed Learning Loop
Learning đã human-approved được promote thành memory draft/facet, embed/retrieve có kiểm soát, ghi MemoryInfluenceManifest và QualityDeltaAttribution.

## Cost Firewall
R3D8 boundary cho future paid provider call: render revision, cost estimate, human paid approval, idempotency, attempt limit, provider/voice/character gates.

## Runtime Dashboard Ops
Dashboard/operator surface đọc trạng thái, queue, handoff, provider readiness và diagnostics. Không tạo job control, không browser automation, không publish/upload tự động.

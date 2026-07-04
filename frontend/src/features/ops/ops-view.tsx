"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Activity, Brain, ClipboardCheck, Database, FileSearch, Gauge, Route, ShieldAlert, UploadCloud } from "lucide-react";

import { EmptyStateCard, MetricSummaryCard, PageHeader, TechnicalAppendix } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import {
  getChannelRuntimeTrace,
  getDiagnosticsQueue,
  getLearningOpsQueue,
  getMemoryInfluenceOps,
  getMemoryOpsQueue,
  getPackageOpsSummary,
  getProviderCostOps,
  getQualityDeltaOps,
  getRecoveryQueue,
  getRetrievalManifestOps,
  getRuntimeOpsCommandCenter,
  getUploadedVideoOpsSummary,
  queryKeys
} from "@/lib/api";
import type { ChannelRuntimeTrace, MemoryInfluenceOps, OpsCard, OpsQueue, PackageOpsSummary, ProviderCostOps, QualityDeltaOps, RetrievalManifestOps, RuntimeOpsCommandCenter, UploadedVideoOpsSummary } from "@/lib/types";

export function OpsView() {
  const search = useSearchParams();
  const command = useQuery({ queryKey: queryKeys.runtimeOpsCommandCenter, queryFn: getRuntimeOpsCommandCenter });

  if (command.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải cockpit vận hành" /></div>;
  if (command.isError) return <div className="p-4 md:p-8"><ErrorState message={command.error.message} /></div>;
  if (!command.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải cockpit vận hành" /></div>;

  const data = command.data;
  const firstChannelId = stringId(search.get("channel") ?? data.active_channels[0]?.entity_id);
  const firstPackageId = stringId(search.get("package") ?? data.packages_waiting_review[0]?.entity_id ?? data.provider_cost_blockers[0]?.entity_id);
  const firstUploadedId = stringId(search.get("uploaded") ?? data.uploaded_videos_waiting_verification_or_analytics[0]?.entity_id);
  const retrievalId = search.get("retrieval");
  const memoryInfluenceId = search.get("memory_influence");
  const qualityDeltaId = search.get("quality_delta");

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Runtime Dashboard Ops"
        subtitle="Cockpit đọc trạng thái runtime, blocker và hành động thủ công an toàn. Trang này không chạy daily, NoView, vector learning, provider hay upload YouTube."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Vận hành" }]}
        meta={<span className="text-xs text-muted-foreground">Cập nhật lúc {formatDate(data.generated_at)}</span>}
      />

      <OpsCommandCenter data={data} />

      <section className="grid gap-4 xl:grid-cols-2">
        <ChannelRuntimeTracePanel channelId={firstChannelId} />
        <PackageOpsPanel packageId={firstPackageId} />
        <UploadedVideoMonitorPanel uploadedVideoId={firstUploadedId} />
        <ProviderCostBoundaryPanel packageId={firstPackageId} />
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <DiagnosticRecoveryPanel />
        <LearningReviewPanel />
        <MemoryApprovalPanel />
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <RetrievalManifestPanel manifestId={retrievalId} />
        <MemoryInfluencePanel manifestId={memoryInfluenceId} />
        <QualityDeltaPanel qualityDeltaId={qualityDeltaId} />
      </section>

      <TechnicalAppendix>
        <KeyValue label="Read model" value="R3D9 Runtime Dashboard Ops" />
        <KeyValue label="Không thêm job-control" value="daily/no-view/vector/provider/upload đều không có nút chạy trên UI" />
        <KeyValue label="Forbidden action count" value={String(data.forbidden_actions.length)} />
        <KeyValue label="Provider/media/upload execution" value={data.technical_appendix.no_provider_media_upload_execution ? "false" : "unknown"} />
      </TechnicalAppendix>
    </div>
  );
}

function OpsCommandCenter({ data }: { data: RuntimeOpsCommandCenter }) {
  const sections = [
    { title: "Gói chờ review", cards: data.packages_waiting_review, icon: ClipboardCheck },
    { title: "Upload thủ công", cards: data.upload_tasks_waiting_human, icon: UploadCloud },
    { title: "Video cần verify/analytics", cards: data.uploaded_videos_waiting_verification_or_analytics, icon: Activity },
    { title: "Diagnostic", cards: data.diagnostics_needing_review, icon: FileSearch },
    { title: "Recovery", cards: data.recovery_proposals_needing_action, icon: Route },
    { title: "Learning", cards: data.learning_candidates_needing_review, icon: Brain },
    { title: "Memory", cards: data.memory_approvals_needing_review, icon: Database },
    { title: "Provider/cost", cards: data.provider_cost_blockers, icon: ShieldAlert },
    { title: "Gate fail", cards: data.gate_failures, icon: Gauge }
  ];
  const totalCards = sections.reduce((sum, section) => sum + section.cards.length, 0);

  return (
    <section className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard label="Kênh active" value={data.active_channels.length} hint="Đọc từ ChannelWorkspace, không đổi contract." />
        <MetricSummaryCard label="Việc cần xử lý" value={totalCards} hint="Mỗi card có next action từ backend." />
        <MetricSummaryCard label="Provider/cost blocker" value={data.provider_cost_blockers.length} hint="Firewall hiển thị will_execute=false." />
        <MetricSummaryCard label="Hành động thủ công" value={data.next_actions.length} hint="Không có auto publish/upload." />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {sections.map((section) => (
          <Panel key={section.title}>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <section.icon size={18} className="text-primary" aria-hidden="true" />
                <h2 className="text-base font-semibold">{section.title}</h2>
              </div>
              <StatusBadge value={section.cards.length ? "ACTION_REQUIRED" : "OK"} />
            </div>
            <div className="mt-4 space-y-3">
              {section.cards.slice(0, 4).map((card) => <OpsCardRow key={card.key} card={card} />)}
              {!section.cards.length ? <p className="text-sm leading-6 text-muted-foreground">Chưa có việc trong nhóm này.</p> : null}
            </div>
          </Panel>
        ))}
      </div>
    </section>
  );
}

function OpsCardRow({ card }: { card: OpsCard }) {
  const body = (
    <div className="rounded-md border border-border bg-background/35 p-3 text-sm transition hover:border-primary/50">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="break-words font-medium">{card.title}</div>
          <p className="mt-1 text-muted-foreground">{card.next_action.next_action_label_vi}</p>
        </div>
        <StatusBadge value={card.status} />
      </div>
      <TechnicalAppendix>
        <KeyValue label="Entity" value={`${card.entity_type}:${card.entity_id ?? "unknown"}`} />
        <KeyValue label="Reason count" value={String(card.blocker_reason_codes.length)} />
      </TechnicalAppendix>
    </div>
  );
  return card.link_target ? <Link href={card.link_target}>{body}</Link> : body;
}

function ChannelRuntimeTracePanel({ channelId }: { channelId?: string | null }) {
  const query = useQuery({
    queryKey: channelId ? queryKeys.channelRuntimeTrace(channelId) : ["channel-runtime-trace", "empty"],
    queryFn: () => getChannelRuntimeTrace(channelId as string),
    enabled: Boolean(channelId)
  });
  if (!channelId) return <EmptyStateCard title="Chưa có runtime trace" description="Khi có channel active có EffectiveChannelRuntimeContextSnapshot, trace sẽ hiển thị contract/context snapshot đã dùng." />;
  if (query.isLoading) return <Panel><LoadingState label="Đang tải runtime trace" /></Panel>;
  if (query.isError) return <Panel><ErrorState message={query.error.message} /></Panel>;
  if (!query.data) return null;
  return <RuntimeTraceContent trace={query.data} />;
}

function RuntimeTraceContent({ trace }: { trace: ChannelRuntimeTrace }) {
  return (
    <Panel>
      <PanelTitle icon={Route} title="Channel Runtime Trace" status={trace.latest_mutable_settings_used ? "BLOCKED" : "READ_ONLY"} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Effective context" value={short(trace.effective_context_snapshot_id)} />
        <InfoLine label="Channel contract hash" value={short(trace.channel_contract_hash)} />
        <InfoLine label="Category" value={short(trace.category_id)} />
        <InfoLine label="Character binding" value={short(trace.character_binding_id)} />
        <InfoLine label="Market/locale/language" value={compactJson(trace.market_locale_language)} />
        <InfoLine label="Voice profile" value={compactJson(trace.voice_profile)} />
        <InfoLine label="Thumbnail style" value={compactJson(trace.thumbnail_style)} />
        <InfoLine label="Publish timing" value={compactJson(trace.publish_timing_policy)} />
      </div>
      <TechnicalAppendix>
        <JsonBlock value={{ snapshot_refs: trace.snapshot_refs, provider_boundary: trace.provider_boundary, budget_cost_policy: trace.budget_cost_policy, source_refs: trace.source_refs, context_hash: trace.context_hash }} />
      </TechnicalAppendix>
    </Panel>
  );
}

function PackageOpsPanel({ packageId }: { packageId?: string | null }) {
  const query = useQuery({
    queryKey: packageId ? queryKeys.packageOpsSummary(packageId) : ["package-ops-summary", "empty"],
    queryFn: () => getPackageOpsSummary(packageId as string),
    enabled: Boolean(packageId)
  });
  if (!packageId) return <EmptyStateCard title="Chưa có package ops summary" description="Khi có package chờ review, panel sẽ hiện handoff M1, gate R3D4 và action upload thủ công." />;
  if (query.isLoading) return <Panel><LoadingState label="Đang tải package ops" /></Panel>;
  if (query.isError) return <Panel><ErrorState message={query.error.message} /></Panel>;
  if (!query.data) return null;
  return <PackageOpsContent summary={query.data} />;
}

function PackageOpsContent({ summary }: { summary: PackageOpsSummary }) {
  return (
    <Panel>
      <PanelTitle icon={ClipboardCheck} title="Package Ops Summary" status={summary.package_status} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Next action" value={summary.next_action.next_action_label_vi} />
        <InfoLine label="Hook / 3 giây đầu" value={compactJson(summary.hook_first_3_seconds)} />
        <InfoLine label="Copy upload" value={compactJson(summary.title_description_subtitles_disclosure)} />
        <InfoLine label="Thumbnail" value={compactJson(summary.thumbnail_handoff)} />
        <InfoLine label="Publish timing" value={compactJson(summary.publish_timing_recommendation)} />
        <InfoLine label="Packaging gates" value={`${summary.packaging_gate_results.length} gate`} />
        <InfoLine label="Agent context packs" value={`${summary.agent_context_pack_refs.length} pack`} />
      </div>
      <TechnicalAppendix>
        <JsonBlock value={{ r3d4: summary.r3d4_deterministic_gate_results, prompt_budget: summary.prompt_budget_summary, manual_publish_handoff: summary.manual_publish_handoff, provider_boundary_summary: summary.provider_boundary_summary }} />
      </TechnicalAppendix>
    </Panel>
  );
}

function UploadedVideoMonitorPanel({ uploadedVideoId }: { uploadedVideoId?: string | null }) {
  const query = useQuery({
    queryKey: uploadedVideoId ? queryKeys.uploadedVideoOpsSummary(uploadedVideoId) : ["uploaded-video-ops-summary", "empty"],
    queryFn: () => getUploadedVideoOpsSummary(uploadedVideoId as string),
    enabled: Boolean(uploadedVideoId)
  });
  if (!uploadedVideoId) return <EmptyStateCard title="Chưa có video cần theo dõi" description="Sau paste-back, panel sẽ hiển thị video_id/URL, backfill history, verify và analytics maturity." />;
  if (query.isLoading) return <Panel><LoadingState label="Đang tải uploaded video monitor" /></Panel>;
  if (query.isError) return <Panel><ErrorState message={query.error.message} /></Panel>;
  if (!query.data) return null;
  return <UploadedVideoMonitorContent summary={query.data} />;
}

function UploadedVideoMonitorContent({ summary }: { summary: UploadedVideoOpsSummary }) {
  return (
    <Panel>
      <PanelTitle icon={UploadCloud} title="Uploaded Video Monitor" status={summary.verification_status} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Platform" value={summary.platform} />
        <InfoLine label="Video" value={summary.platform_video_id} />
        <InfoLine label="Analytics" value={`${summary.analytics_maturity} / ${summary.analytics_confidence}`} />
        <InfoLine label="Timezone" value={`${summary.channel_timezone ?? "?"} -> ${summary.operator_timezone ?? "?"}`} />
        <InfoLine label="Next action" value={summary.next_action.next_action_label_vi} />
        <InfoLine label="Backfill events" value={String(summary.backfill_history.length)} />
      </div>
      <TechnicalAppendix>
        <JsonBlock value={{ linked_package_project: summary.linked_package_project, diagnostics: summary.diagnostics, recovery: summary.recovery_proposal_refs, learning: summary.learning_candidate_refs, no_youtube_studio_scraping: summary.no_youtube_studio_scraping }} />
      </TechnicalAppendix>
    </Panel>
  );
}

function DiagnosticRecoveryPanel() {
  const diagnostics = useQuery({ queryKey: queryKeys.diagnosticsQueue, queryFn: getDiagnosticsQueue });
  const recovery = useQuery({ queryKey: queryKeys.recoveryQueue, queryFn: getRecoveryQueue });
  return (
    <Panel>
      <PanelTitle icon={FileSearch} title="Diagnostic / Recovery" status={(diagnostics.data?.items.length ?? 0) + (recovery.data?.items.length ?? 0) ? "ACTION_REQUIRED" : "OK"} />
      <QueueList label="Diagnostic" query={diagnostics} empty="Chưa có diagnostic cần review." />
      <QueueList label="Recovery" query={recovery} empty="Chưa có recovery proposal cần xử lý." />
    </Panel>
  );
}

function LearningReviewPanel() {
  const query = useQuery({ queryKey: queryKeys.learningOpsQueue, queryFn: getLearningOpsQueue });
  return (
    <Panel>
      <PanelTitle icon={Brain} title="Learning Review Queue" status={query.data?.items.length ? "REVIEW_REQUIRED" : "OK"} />
      <QueueList label="Learning" query={query} empty="Chưa có learning candidate chờ duyệt." />
    </Panel>
  );
}

function MemoryApprovalPanel() {
  const query = useQuery({ queryKey: queryKeys.memoryOpsQueue, queryFn: getMemoryOpsQueue });
  return (
    <Panel>
      <PanelTitle icon={Database} title="Memory Approval Queue" status={query.data?.items.length ? "REVIEW_REQUIRED" : "OK"} />
      <p className="mt-3 text-sm leading-6 text-muted-foreground">{query.data?.prompt_eligibility_rule_vi ?? "Chỉ memory đã duyệt, an toàn, prompt-safe và fresh mới có thể dùng về sau."}</p>
      <QueueList label="Memory" query={query} empty="Chưa có memory item chờ duyệt." />
    </Panel>
  );
}

function RetrievalManifestPanel({ manifestId }: { manifestId?: string | null }) {
  const query = useQuery({
    queryKey: manifestId ? queryKeys.retrievalManifestOps(manifestId) : ["retrieval-manifest-ops", "empty"],
    queryFn: () => getRetrievalManifestOps(manifestId as string),
    enabled: Boolean(manifestId)
  });
  if (!manifestId) return <EmptyStateCard title="Retrieval manifest debug" description="Chưa chọn retrieval manifest để inspect. Khi mở từ package/agent trace, raw memory text vẫn được ẩn mặc định." />;
  if (query.isLoading) return <Panel><LoadingState label="Đang tải retrieval manifest" /></Panel>;
  if (query.isError) return <Panel><ErrorState message={query.error.message} /></Panel>;
  if (!query.data) return null;
  return <RetrievalManifestContent data={query.data} />;
}

function RetrievalManifestContent({ data }: { data: RetrievalManifestOps }) {
  return (
    <Panel>
      <PanelTitle icon={FileSearch} title="Retrieval Manifest Debug" status={data.raw_memory_hidden ? "READ_ONLY" : "BLOCKED"} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Agent/use case" value={`${data.agent_key} / ${data.use_case}`} />
        <InfoLine label="Candidate count" value={`${data.candidate_count_before_vector} -> ${data.candidate_count_after_policy}`} />
        <InfoLine label="Selected facets" value={String(data.selected_facets.length)} />
        <InfoLine label="Raw memory" value={data.raw_memory_hidden ? "Đã ẩn mặc định" : "Cần kiểm tra"} />
      </div>
      <TechnicalAppendix>
        <JsonBlock value={{ sql_filter: data.sql_filter, selected_facets: data.selected_facets, blocked_refs: data.blocked_refs, rejected_refs: data.rejected_refs, retrieval_hash: data.retrieval_hash, digest_hash: data.digest_hash }} />
      </TechnicalAppendix>
    </Panel>
  );
}

function MemoryInfluencePanel({ manifestId }: { manifestId?: string | null }) {
  const query = useQuery({
    queryKey: manifestId ? queryKeys.memoryInfluenceOps(manifestId) : ["memory-influence-ops", "empty"],
    queryFn: () => getMemoryInfluenceOps(manifestId as string),
    enabled: Boolean(manifestId)
  });
  if (!manifestId) return <EmptyStateCard title="Memory Influence" description="Chưa chọn influence manifest. Khi có manifest, panel sẽ cho thấy agent/package, facet refs và scope status." />;
  if (query.isLoading) return <Panel><LoadingState label="Đang tải memory influence" /></Panel>;
  if (query.isError) return <Panel><ErrorState message={query.error.message} /></Panel>;
  if (!query.data) return null;
  return <MemoryInfluenceContent data={query.data} />;
}

function MemoryInfluenceContent({ data }: { data: MemoryInfluenceOps }) {
  return (
    <Panel>
      <PanelTitle icon={Brain} title="Memory Influence" status={data.scope_status} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Agent" value={data.agent_key} />
        <InfoLine label="Facets used" value={String(data.memory_facets_used.length)} />
        <InfoLine label="Next action" value={data.next_action.next_action_label_vi} />
        <InfoLine label="Retrieval manifest" value={short(data.retrieval_manifest_id)} />
      </div>
      <TechnicalAppendix>
        <JsonBlock value={{ applied_as: data.applied_as, memory_facets_used: data.memory_facets_used, ignored: data.ignored_memory_refs, blocked: data.blocked_memory_refs, digest_hash: data.digest_hash, prompt_context_hash: data.prompt_context_hash }} />
      </TechnicalAppendix>
    </Panel>
  );
}

function QualityDeltaPanel({ qualityDeltaId }: { qualityDeltaId?: string | null }) {
  const query = useQuery({
    queryKey: qualityDeltaId ? queryKeys.qualityDeltaOps(qualityDeltaId) : ["quality-delta-ops", "empty"],
    queryFn: () => getQualityDeltaOps(qualityDeltaId as string),
    enabled: Boolean(qualityDeltaId)
  });
  if (!qualityDeltaId) return <EmptyStateCard title="Quality Delta Attribution" description="Chưa chọn quality delta. Khi có attribution, panel sẽ phân biệt cải thiện, giảm chất lượng, quá sớm hoặc bị chặn do dữ liệu." />;
  if (query.isLoading) return <Panel><LoadingState label="Đang tải quality delta" /></Panel>;
  if (query.isError) return <Panel><ErrorState message={query.error.message} /></Panel>;
  if (!query.data) return null;
  return <QualityDeltaContent data={query.data} />;
}

function QualityDeltaContent({ data }: { data: QualityDeltaOps }) {
  return (
    <Panel>
      <PanelTitle icon={Gauge} title="Quality Delta" status={data.result} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Metric" value={`${data.expected_metric_family} / ${data.expected_direction}`} />
        <InfoLine label="Confidence delta" value={String(data.confidence_delta)} />
        <InfoLine label="Next action" value={data.next_action.next_action_label_vi} />
        <InfoLine label="Facets used" value={String(data.memory_facets_used.length)} />
      </div>
      <TechnicalAppendix>
        <JsonBlock value={{ baseline: data.baseline_snapshot, observed: data.observed_snapshot, reason_codes: data.reason_codes, memory_facets_used: data.memory_facets_used }} />
      </TechnicalAppendix>
    </Panel>
  );
}

function ProviderCostBoundaryPanel({ packageId }: { packageId?: string | null }) {
  const query = useQuery({
    queryKey: packageId ? queryKeys.providerCostOps(packageId) : ["provider-cost-ops", "empty"],
    queryFn: () => getProviderCostOps(packageId as string),
    enabled: Boolean(packageId)
  });
  if (!packageId) return <EmptyStateCard title="Provider / Cost Firewall" description="Khi package có render revision/cost boundary, panel sẽ hiện readiness, approval, attempt limit và will_execute=false." />;
  if (query.isLoading) return <Panel><LoadingState label="Đang tải provider/cost boundary" /></Panel>;
  if (query.isError) return <Panel><ErrorState message={query.error.message} /></Panel>;
  if (!query.data) return null;
  return <ProviderCostBoundaryContent data={query.data} />;
}

function ProviderCostBoundaryContent({ data }: { data: ProviderCostOps }) {
  return (
    <Panel>
      <PanelTitle icon={ShieldAlert} title="Provider / Cost Firewall" status={data.will_execute ? "BLOCKED" : "READ_ONLY"} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Will execute" value={data.will_execute ? "Có" : "Không"} />
        <InfoLine label="Missing config" value={data.missing_config.length ? `${data.missing_config.length} mục` : "Không có blocker từ read-model"} />
        <InfoLine label="Render revisions" value={String(data.render_revisions.length)} />
        <InfoLine label="Cost estimates" value={String(data.cost_estimates.length)} />
        <InfoLine label="Paid approvals" value={String(data.human_paid_render_approvals.length)} />
        <InfoLine label="Next action" value={data.next_action.next_action_label_vi} />
      </div>
      <TechnicalAppendix>
        <JsonBlock value={{ provider_readiness: data.provider_readiness, cost_estimates: data.cost_estimates, approvals: data.human_paid_render_approvals, attempt_limits: data.paid_attempt_limits, ledger: data.paid_provider_call_ledger, proxy_preview_flags: data.proxy_preview_flags, technical: data.technical_appendix }} />
      </TechnicalAppendix>
    </Panel>
  );
}

function QueueList({ label, query, empty }: { label: string; query: ReturnType<typeof useQuery<OpsQueue>>; empty: string }) {
  if (query.isLoading) return <div className="mt-4"><LoadingState label={`Đang tải ${label}`} /></div>;
  if (query.isError) return <div className="mt-4"><ErrorState message={query.error.message} /></div>;
  const items = query.data?.items ?? [];
  return (
    <div className="mt-4 space-y-2">
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      {items.slice(0, 4).map((item, index) => (
        <div key={String(item.id ?? item.queue_item_id ?? index)} className="rounded-md border border-border bg-background/35 p-3 text-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="break-words font-medium">{String(item.operator_summary ?? item.learning_candidate ?? item.summary ?? item.proposed_recovery ?? "Việc cần review")}</div>
              <p className="mt-1 text-muted-foreground">{nextActionLabel(item)}</p>
            </div>
            <StatusBadge value={String(item.approval_status ?? item.data_maturity ?? item.gate_result_summary ?? "PENDING")} />
          </div>
          <TechnicalAppendix>
            <JsonBlock value={item} />
          </TechnicalAppendix>
        </div>
      ))}
      {!items.length ? <p className="text-sm leading-6 text-muted-foreground">{empty}</p> : null}
    </div>
  );
}

function PanelTitle({ icon: Icon, title, status }: { icon: typeof Activity; title: string; status?: string | null }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-2">
        <Icon size={18} className="text-primary" aria-hidden="true" />
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      <StatusBadge value={status ?? "UNKNOWN"} />
    </div>
  );
}

function InfoLine({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-md border border-border/70 bg-muted/20 p-3">
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 break-words">{value ? String(value) : "Chưa có dữ liệu"}</div>
    </div>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span>{value}</span>
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/30 p-3 text-xs">{JSON.stringify(value, null, 2)}</pre>;
}

function nextActionLabel(item: Record<string, unknown>) {
  const next = item.next_action;
  if (next && typeof next === "object" && "next_action_label_vi" in next) {
    return String((next as { next_action_label_vi?: unknown }).next_action_label_vi ?? "Xem chi tiết");
  }
  return "Xem chi tiết và xử lý thủ công nếu được phép.";
}

function stringId(value: unknown) {
  return typeof value === "string" && value ? value : null;
}

function compactJson(value: unknown) {
  if (!value) return "Chưa có dữ liệu";
  const text = JSON.stringify(value);
  return text.length > 160 ? `${text.slice(0, 160)}...` : text;
}

function short(value?: string | null) {
  return value ? value.slice(0, 12) : "Chưa có";
}

function formatDate(value?: string | null) {
  if (!value) return "Chưa có dữ liệu";
  return new Date(value).toLocaleString("vi-VN");
}

"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clipboard, Clock, FileText, ImageIcon, MessageSquare, RefreshCw, ShieldAlert, ShieldCheck, UploadCloud, X, type LucideIcon } from "lucide-react";

import { EmptyStateCard, MetricSummaryCard, PageHeader, TechnicalAppendix } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import {
  applyApprovedChangesAndRecheckPackage,
  approvePackagingProposedPatch,
  buildPackagingReviewQueueFromGates,
  createUploadTaskFromPackage,
  getVideoPackageReview,
  queryKeys,
  rejectPackagingProposedPatch,
  requestChangesPackagingProposedPatch
} from "@/lib/api";
import type { PackagingApplyApprovedChangesResult, PackagingGateResult, PackagingHandoff, PackagingReviewQueue, PackagingReviewQueueItem } from "@/lib/types";

export function PackageReviewView({ packageId }: { packageId: string }) {
  const queryClient = useQueryClient();
  const [taskMessage, setTaskMessage] = useState<string | null>(null);
  const [queueMessage, setQueueMessage] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<PackagingApplyApprovedChangesResult | null>(null);
  const query = useQuery({
    queryKey: queryKeys.videoPackageReview(packageId),
    queryFn: () => getVideoPackageReview(packageId)
  });
  const taskMutation = useMutation({
    mutationFn: () => createUploadTaskFromPackage(packageId),
    onSuccess: async (task) => {
      setTaskMessage(`Task upload thủ công đã sẵn sàng: ${task.id.slice(0, 8)}. ${task.next_action}`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.videoPackageReview(packageId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.packagingReviewQueue(packageId) });
    }
  });
  const buildQueueMutation = useMutation({
    mutationFn: () => buildPackagingReviewQueueFromGates(packageId),
    onSuccess: async (queue) => {
      setQueueMessage(`Hàng chờ review đã cập nhật: ${queue.must_fix_count} mục cần xử lý.`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.videoPackageReview(packageId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.packagingReviewQueue(packageId) });
    }
  });
  const patchDecisionMutation = useMutation({
    mutationFn: async ({ patchId, action }: { patchId: string; action: "APPROVE" | "REJECT" | "REQUEST_CHANGES" }) => {
      if (action === "APPROVE") return approvePackagingProposedPatch(patchId, "Duyệt từ packaging review cockpit.");
      if (action === "REJECT") return rejectPackagingProposedPatch(patchId, "Từ chối từ packaging review cockpit.");
      return requestChangesPackagingProposedPatch(patchId, "Cần chỉnh proposal trước khi duyệt.");
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.videoPackageReview(packageId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.packagingReviewQueue(packageId) });
    }
  });
  const applyRecheckMutation = useMutation({
    mutationFn: () => applyApprovedChangesAndRecheckPackage(packageId),
    onSuccess: async (result) => {
      setApplyResult(result);
      await queryClient.invalidateQueries({ queryKey: queryKeys.videoPackageReview(packageId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.packagingReviewQueue(packageId) });
    }
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải gói handoff" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  const handoff = query.data?.packaging_handoff;
  if (!handoff) {
    return (
      <div className="p-4 md:p-8">
        <EmptyStateCard
          title="Chưa có handoff package"
          description="Gói này chưa có dữ liệu packaging handoff. Cần chạy lại package review hoặc kiểm tra artifact nguồn trước khi upload thủ công."
          actions={[{ label: "Về gói publish", href: "/publishing", variant: "primary" }]}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6 p-4 md:p-8">
      {(() => {
        const uploadState = uploadButtonState(handoff, query.data?.packaging_review_queue ?? null, taskMutation.isPending);
        return (
      <PageHeader
        title="Handoff upload thủ công"
        subtitle="Review hook, copy, thumbnail, thời điểm publish khuyến nghị và gate packaging trước khi upload ngoài VCOS."
        breadcrumbs={[{ label: "Gói publish", href: "/publishing" }, { label: packageId.slice(0, 8) }]}
        primaryAction={
          <Button variant="primary" onClick={() => taskMutation.mutate()} disabled={uploadState.disabled}>
            <UploadCloud size={16} aria-hidden="true" /> {uploadState.label}
          </Button>
        }
      />
        );
      })()}
      <ReviewVerdictCard handoff={handoff} queue={query.data?.packaging_review_queue ?? null} />
      <ApplyApprovedChangesCard
        queue={query.data?.packaging_review_queue ?? null}
        pending={applyRecheckMutation.isPending}
        result={applyResult}
        onApply={() => applyRecheckMutation.mutate()}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={ShieldCheck} label="Trạng thái package" value={<StatusBadge value={handoff.package_status} />} hint="Human final approval vẫn bắt buộc." />
        <MetricSummaryCard icon={Check} label="Gate packaging" value={<StatusBadge value={handoff.packaging_gate_summary.overall_status} />} hint={handoff.packaging_gate_summary.next_action_vi} />
        <MetricSummaryCard icon={Clock} label="Publish thủ công" value={handoff.publish_timing_recommendation.channel_timezone ?? "Chưa có timezone"} hint="VCOS chỉ nhắc thời điểm, không schedule." />
        <MetricSummaryCard icon={UploadCloud} label="Paste-back" value={String(handoff.manual_upload.task_status ?? "Chưa tạo task")} hint={String(handoff.manual_upload.next_action_vi ?? "Nhập URL/video_id sau khi upload thủ công.")} />
      </div>
      {taskMessage ? <div className="rounded-md border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-100">{taskMessage}</div> : null}
      {taskMutation.isError ? <div className="rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">{taskMutation.error.message}</div> : null}
      {queueMessage ? <div className="rounded-md border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-100">{queueMessage}</div> : null}
      {buildQueueMutation.isError ? <div className="rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">{buildQueueMutation.error.message}</div> : null}
      {patchDecisionMutation.isError ? <div className="rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">{patchDecisionMutation.error.message}</div> : null}
      {applyRecheckMutation.isError ? <div className="rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">{applyRecheckMutation.error.message}</div> : null}

      <MustFixBeforeUploadPanel
        handoff={handoff}
        queue={query.data?.packaging_review_queue ?? null}
        buildQueuePending={buildQueueMutation.isPending}
        patchDecisionPending={patchDecisionMutation.isPending}
        onBuildQueue={() => buildQueueMutation.mutate()}
        onPatchDecision={(patchId, action) => patchDecisionMutation.mutate({ patchId, action })}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <HookReviewPanel handoff={handoff} />
        <UploadCopyPanel handoff={handoff} />
        <ThumbnailPanel handoff={handoff} />
        <PublishTimingPanel handoff={handoff} />
      </div>
      <TechnicalGateDetails handoff={handoff} />
      <TechnicalAppendix>
        <KeyValue label="Package ID" value={handoff.package_id} mono />
        <KeyValue label="Effective context" value={handoff.effective_context_snapshot_id ?? "Chưa có"} mono />
        <KeyValue label="Context hash" value={handoff.effective_context_hash ?? "Chưa có"} mono />
        <KeyValue label="Manual-only" value={handoff.manual_publish_only ? "true" : "false"} />
        <KeyValue label="No upload/publish calls" value={handoff.no_upload_or_publish_calls_made ? "true" : "false"} />
      </TechnicalAppendix>
    </div>
  );
}

function ReviewVerdictCard({ handoff, queue }: { handoff: PackagingHandoff; queue?: PackagingReviewQueue | null }) {
  const verdict = queue?.review_verdict ?? fallbackVerdict(handoff);
  const mustFix = queue?.must_fix_count ?? failingGateItems(handoff).length;
  const uploadAllowed = queue?.upload_task_creation_allowed ?? handoff.packaging_gate_summary.overall_status === "PASS";
  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2 text-xs uppercase text-muted-foreground">
            <ShieldAlert size={14} aria-hidden="true" />
            Review verdict
          </div>
          <h2 className="mt-2 text-xl font-semibold">{queue?.plain_language_status ?? verdictLabel(verdict)}</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{queue?.next_safe_action ?? handoff.packaging_gate_summary.next_action_vi}</p>
        </div>
        <StatusBadge value={verdict} />
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <InfoLine label="Must-fix" value={`${mustFix} mục`} compact />
        <InfoLine label="Tạo upload task" value={uploadAllowed ? "Được phép" : "Chưa được phép"} compact />
        <InfoLine label="Manual-only" value={handoff.manual_publish_only ? "Có" : "Cần kiểm tra"} compact />
      </div>
    </Panel>
  );
}

function ApplyApprovedChangesCard({
  queue,
  pending,
  result,
  onApply
}: {
  queue?: PackagingReviewQueue | null;
  pending: boolean;
  result?: PackagingApplyApprovedChangesResult | null;
  onApply: () => void;
}) {
  const label = pending ? "Đang apply và kiểm tra lại..." : queue?.apply_approved_changes_label ?? "Chưa có patch được duyệt";
  const disabled = pending || !queue?.can_apply_approved_changes;
  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2 text-xs uppercase text-muted-foreground">
            <ShieldCheck size={14} aria-hidden="true" />
            Hành động review
          </div>
          <h2 className="mt-2 text-base font-semibold">Apply approved changes & recheck package</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">Áp dụng các patch đã được duyệt và kiểm tra lại package.</p>
        </div>
        <Button type="button" variant="primary" onClick={onApply} disabled={disabled}>
          <RefreshCw size={14} aria-hidden="true" /> {label}
        </Button>
      </div>
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-5">
        <InfoLine label="Đã duyệt" value={queue?.approved_patch_count ?? 0} compact />
        <InfoLine label="Chưa quyết định" value={queue?.ready_for_review_patch_count ?? 0} compact />
        <InfoLine label="Đã từ chối" value={queue?.rejected_patch_count ?? 0} compact />
        <InfoLine label="Cần chỉnh" value={queue?.request_changes_patch_count ?? 0} compact />
        <InfoLine label="Đã apply" value={queue?.applied_patch_count ?? 0} compact />
      </div>
      {!queue?.can_apply_approved_changes && queue?.apply_approved_changes_disabled_reason ? (
        <p className="mt-3 text-sm text-muted-foreground">{queue.apply_approved_changes_disabled_reason}</p>
      ) : null}
      {result ? (
        <div className="mt-4 rounded-md border border-border/70 bg-muted/20 p-3 text-sm">
          <div className="font-medium">{applyResultLabel(result.status)}</div>
          <div className="mt-2 grid gap-2 md:grid-cols-4">
            <InfoLine label="Patch đã apply" value={`${result.applied_patch_ids.length} mục`} compact />
            <InfoLine label="Kiểm tra gate" value={result.gate_rerun_record_ids.length ? `${result.gate_rerun_record_ids.length} lượt` : "Không chạy"} compact />
            <InfoLine label="Blocker còn lại" value={`${result.remaining_blockers.length} mục`} compact />
            <InfoLine label="Task thủ công" value={result.upload_task_creation_allowed ? "Được phép" : "Chưa được phép"} compact />
          </div>
          <p className="mt-2 text-muted-foreground">{result.next_safe_action}</p>
        </div>
      ) : null}
    </Panel>
  );
}

function MustFixBeforeUploadPanel({
  handoff,
  queue,
  buildQueuePending,
  patchDecisionPending,
  onBuildQueue,
  onPatchDecision
}: {
  handoff: PackagingHandoff;
  queue?: PackagingReviewQueue | null;
  buildQueuePending: boolean;
  patchDecisionPending: boolean;
  onBuildQueue: () => void;
  onPatchDecision: (patchId: string, action: "APPROVE" | "REJECT" | "REQUEST_CHANGES") => void;
}) {
  const items = reviewDisplayItems(handoff, queue);
  const unresolved = items.filter((item) => item.status !== "CLOSED");
  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PanelHeading icon={ShieldAlert} title="Must Fix Before Upload" status={unresolved.length ? "REVIEW_REQUIRED" : "PASS"} />
        <Button type="button" variant="secondary" onClick={onBuildQueue} disabled={buildQueuePending}>
          <RefreshCw size={14} aria-hidden="true" /> {buildQueuePending ? "Đang cập nhật" : "Cập nhật hàng chờ từ gate"}
        </Button>
      </div>
      <div className="mt-4 space-y-3">
        {unresolved.map((item) => {
          const readyPatch = item.proposed_patch?.status === "READY_FOR_REVIEW" ? item.proposed_patch : null;
          return (
            <div key={item.id} className="rounded-md border border-border bg-background/40 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="break-words text-base font-semibold">{item.human_readable_title}</h3>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">{item.human_readable_why}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <StatusBadge value={item.severity} />
                  <StatusBadge value={item.status} />
                </div>
              </div>
              <div className="mt-3 grid gap-3 text-sm md:grid-cols-3">
                <InfoLine label="Target section" value={item.section} compact />
                <InfoLine label="Linked gate" value={item.gate_key} compact />
                <InfoLine label="Issue" value={item.issue_code} compact />
              </div>
              <div className="mt-3 rounded-md border border-border/70 bg-muted/20 p-3 text-sm">
                <div className="text-xs uppercase text-muted-foreground">Proposed fix</div>
                <div className="mt-1">{readyPatch ? patchSummary(readyPatch.after_preview_json || readyPatch.proposed_patch_json) : missingPatchReason(item)}</div>
                <div className="mt-2 text-muted-foreground">{item.human_readable_fix}</div>
              </div>
              {readyPatch ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button type="button" variant="primary" disabled={patchDecisionPending} onClick={() => onPatchDecision(readyPatch.id, "APPROVE")}>
                    <Check size={14} aria-hidden="true" /> Approve
                  </Button>
                  <Button type="button" variant="secondary" disabled={patchDecisionPending} onClick={() => onPatchDecision(readyPatch.id, "REJECT")}>
                    <X size={14} aria-hidden="true" /> Reject
                  </Button>
                  <Button type="button" variant="secondary" disabled={patchDecisionPending} onClick={() => onPatchDecision(readyPatch.id, "REQUEST_CHANGES")}>
                    <MessageSquare size={14} aria-hidden="true" /> Request changes
                  </Button>
                </div>
              ) : null}
            </div>
          );
        })}
        {!unresolved.length ? <p className="text-sm leading-6 text-muted-foreground">Không còn mục must-fix trước upload thủ công.</p> : null}
      </div>
    </Panel>
  );
}

function HookReviewPanel({ handoff }: { handoff: PackagingHandoff }) {
  const hookGate = findGate(handoff, "HookTruthfulnessGate") ?? findGate(handoff, "HookPayoffGate");
  const hook = handoff.hook_spec;
  return (
    <Panel>
      <PanelHeading icon={FileText} title="Review hook / 3 giây đầu" status={hookGate?.status} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Loại hook" value={hook.hook_type} />
        <InfoLine label="Script 3 giây đầu" value={hook.first_3_seconds_script} />
        <InfoLine label="Visual 3 giây đầu" value={hook.first_3_seconds_visual} />
        <InfoLine label="Promise" value={hook.promise_made} />
        <InfoLine label="Payoff" value={hook.payoff_location} />
        <InfoLine label="Clickbait risk" value={hook.clickbait_risk} />
      </div>
      <GateHint gate={hookGate} />
    </Panel>
  );
}

function UploadCopyPanel({ handoff }: { handoff: PackagingHandoff }) {
  const copy = handoff.upload_handoff_copy;
  const checklist = copy.checklist_items_json.map((item) => String(item.item ?? item.value ?? JSON.stringify(item))).join("\n");
  return (
    <Panel>
      <PanelHeading icon={Clipboard} title="Copy upload sang YouTube" status={copy.packaging_gate_status} />
      <div className="mt-4 space-y-4 text-sm">
        <CopyBlock label="Title" value={copy.title ?? ""} />
        <CopyBlock label="Description" value={copy.description ?? ""} multiline />
        {copy.hashtags_json?.length ? <CopyBlock label="Hashtags" value={copy.hashtags_json.join(" ")} /> : null}
        <JsonList title="Subtitle refs" items={copy.subtitle_refs_json} />
        <JsonList title="Disclosure notes" items={copy.disclosure_notes_json} />
        <CopyBlock label="Checklist copy" value={checklist} multiline />
        <div className="grid gap-2 md:grid-cols-3">
          <InfoLine label="Language" value={copy.language} compact />
          <InfoLine label="Locale" value={copy.locale} compact />
          <InfoLine label="Contract hash" value={shortValue(copy.channel_contract_hash)} compact />
        </div>
      </div>
    </Panel>
  );
}

function ThumbnailPanel({ handoff }: { handoff: PackagingHandoff }) {
  const thumb = handoff.thumbnail_handoff;
  const gate = findGate(handoff, "ThumbnailTruthfulnessGate") ?? findGate(handoff, "MobileThumbnailLegibilityGate");
  return (
    <Panel>
      <PanelHeading icon={ImageIcon} title="Handoff thumbnail" status={gate?.status} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Concept" value={thumb.concept} />
        <InfoLine label="Text overlay" value={thumb.text_overlay} />
        <InfoLine label="Main subject" value={thumb.main_subject} />
        <InfoLine label="Composition" value={thumb.composition} />
        <InfoLine label="Mobile readability" value={thumb.mobile_readability_notes} />
        <InfoLine label="Thumbnail ref" value={safeJson(thumb.thumbnail_ref)} />
        <InfoLine label="Drive ref" value={safeJson(thumb.drive_ref)} />
        <InfoLine label="Character branch" value={thumb.character_image_branch_id} />
        <InfoLine label="Reference asset pack" value={thumb.reference_asset_pack_id} />
      </div>
      <GateHint gate={gate} />
    </Panel>
  );
}

function PublishTimingPanel({ handoff }: { handoff: PackagingHandoff }) {
  const timing = handoff.publish_timing_recommendation;
  const gate = findGate(handoff, "PublishTimingComplianceGate") ?? findGate(handoff, "ManualPublishOnlyGate");
  return (
    <Panel>
      <PanelHeading icon={Clock} title="Thời điểm publish khuyến nghị" status={gate?.status} />
      <div className="mt-4 grid gap-3 text-sm">
        <InfoLine label="Channel timezone" value={timing.channel_timezone} />
        <InfoLine label="Audience timezone" value={timing.audience_timezone} />
        <InfoLine label="Recommended window" value={safeJson(timing.configured_publish_window_json)} />
        <InfoLine label="Channel time" value={formatDate(timing.suggested_publish_time_channel_tz)} />
        <InfoLine label="Operator local" value={formatDate(timing.suggested_publish_time_operator_local)} />
        <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-amber-100">
          Chỉ publish thủ công. VCOS không upload, không publish và không schedule trên YouTube.
        </div>
        <InfoLine label="Task upload" value={String(handoff.manual_upload.human_upload_task_id ?? "Chưa tạo")} />
        <InfoLine label="YouTube video_id" value={String(handoff.manual_upload.youtube_video_id ?? "Chưa paste-back")} />
      </div>
      <GateHint gate={gate} />
    </Panel>
  );
}

function TechnicalGateDetails({ handoff }: { handoff: PackagingHandoff }) {
  return (
    <details className="rounded-md border border-border bg-card p-4">
      <summary className="cursor-pointer text-sm font-medium text-muted-foreground">Chi tiết kỹ thuật</summary>
      <div className="mt-4">
        <PanelHeading icon={ShieldCheck} title="Raw gate packaging" status={handoff.packaging_gate_summary.overall_status} />
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[920px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              {["Gate", "Status", "Lý do", "Artifact", "Contract paths", "Việc tiếp theo"].map((header) => (
                <th key={header} className="px-3 py-2 font-medium">{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {handoff.packaging_gate_summary.gate_results.map((gate) => (
              <tr key={gate.gate_key} className="border-b border-border/60">
                <td className="px-3 py-3 font-medium">{gate.gate_key}</td>
                <td className="px-3 py-3"><StatusBadge value={gate.status} /></td>
                <td className="px-3 py-3">{gate.reason_codes.length ? gate.reason_codes.join(", ") : "Không có blocker"}</td>
                <td className="px-3 py-3">{gate.checked_artifact_refs.map((ref) => String(ref.artifact_key ?? ref.ref ?? "")).filter(Boolean).join(", ")}</td>
                <td className="px-3 py-3">{gate.checked_contract_paths.join(", ")}</td>
                <td className="px-3 py-3">{gate.next_action_vi ?? gate.summary_vi}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function CopyBlock({ label, value, multiline }: { label: string; value: string; multiline?: boolean }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs uppercase text-muted-foreground">{label}</div>
        <CopyButton value={value} label={`Copy ${label}`} />
      </div>
      <div className={multiline ? "mt-2 whitespace-pre-wrap leading-6" : "mt-2 font-medium"}>{value || "Chưa có dữ liệu"}</div>
    </div>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      onClick={async () => {
        await navigator.clipboard?.writeText(value);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      }}
      disabled={!value}
    >
      <Clipboard size={14} aria-hidden="true" /> {copied ? "Đã copy" : label}
    </Button>
  );
}

function PanelHeading({ icon: Icon, title, status }: { icon: LucideIcon; title: string; status?: string | null }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-center gap-2">
        <Icon size={18} className="text-primary" aria-hidden="true" />
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      {status ? <StatusBadge value={status} /> : null}
    </div>
  );
}

function GateHint({ gate }: { gate?: PackagingGateResult }) {
  if (!gate) return null;
  return (
    <div className="mt-4 rounded-md border border-border bg-background/45 p-3 text-sm">
      <div className="font-medium">{gate.summary_vi}</div>
      {gate.reason_codes.length ? <div className="mt-1 text-muted-foreground">Mã lý do: {gate.reason_codes.join(", ")}</div> : null}
    </div>
  );
}

function JsonList({ title, items }: { title: string; items: Array<Record<string, unknown>> }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="text-xs uppercase text-muted-foreground">{title}</div>
      {items.length ? (
        <ul className="mt-2 space-y-1">
          {items.map((item, index) => (
            <li key={index} className="break-words">{safeJson(item)}</li>
          ))}
        </ul>
      ) : (
        <div className="mt-2 text-muted-foreground">Chưa có dữ liệu</div>
      )}
    </div>
  );
}

function InfoLine({ label, value, compact }: { label: string; value: unknown; compact?: boolean }) {
  return (
    <div className={compact ? "" : "rounded-md border border-border/70 bg-muted/20 p-3"}>
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 break-words">{value ? String(value) : "Chưa có dữ liệu"}</div>
    </div>
  );
}

function KeyValue({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span className={mono ? "font-mono text-xs" : ""}>{value}</span>
    </div>
  );
}

function findGate(handoff: PackagingHandoff, gateKey: string) {
  return handoff.packaging_gate_summary.gate_results.find((gate) => gate.gate_key === gateKey);
}

function uploadButtonState(handoff: PackagingHandoff, queue: PackagingReviewQueue | null, pending: boolean) {
  const verdict = queue?.review_verdict ?? fallbackVerdict(handoff);
  if (pending) return { disabled: true, label: "Đang tạo task" };
  if (verdict === "BLOCKED") return { disabled: true, label: "Đang bị block" };
  if (verdict === "REVIEW_REQUIRED") return { disabled: true, label: "Còn mục cần review" };
  if (verdict === "WAITING_PROVIDER_CONFIG") return { disabled: true, label: "Chờ cấu hình provider" };
  return { disabled: false, label: "Tạo task upload thủ công" };
}

function fallbackVerdict(handoff: PackagingHandoff) {
  const status = handoff.packaging_gate_summary.overall_status;
  if (handoff.package_status === "WAITING_PROVIDER_CONFIG") return "WAITING_PROVIDER_CONFIG";
  if (status === "BLOCK") return "BLOCKED";
  if (status === "REVIEW_REQUIRED") return "REVIEW_REQUIRED";
  return "READY_FOR_MANUAL_UPLOAD";
}

function verdictLabel(verdict: string) {
  const labels: Record<string, string> = {
    READY_FOR_MANUAL_UPLOAD: "Sẵn sàng tạo task upload thủ công.",
    REVIEW_REQUIRED: "Còn mục cần review trước upload.",
    BLOCKED: "Đang bị block trước upload.",
    WAITING_PROVIDER_CONFIG: "Đang chờ cấu hình provider."
  };
  return labels[verdict] ?? "Chưa rõ trạng thái review.";
}

function applyResultLabel(status: string) {
  const labels: Record<string, string> = {
    APPLIED_AND_RECHECKED: "Đã apply patch được duyệt và kiểm tra lại.",
    BLOCKED_WAITING_HUMAN_APPROVAL: "Chưa có patch được duyệt.",
    BLOCKED_PENDING_HUMAN_DECISIONS: "Còn patch chưa quyết định.",
    APPLY_FAILED: "Apply chưa thành công.",
    NOOP_ALREADY_APPLIED: "Không có patch mới cần apply."
  };
  return labels[status] ?? "Đã nhận kết quả apply/recheck.";
}

function reviewDisplayItems(handoff: PackagingHandoff, queue?: PackagingReviewQueue | null): PackagingReviewQueueItem[] {
  if (queue?.items.length) return queue.items;
  return failingGateItems(handoff).map((gate, index) => {
    const issue = gate.reason_codes[0] ?? "PACKAGING_GATE_REVIEW_REQUIRED";
    const copy = issueCopy(issue);
    const target = gate.checked_artifact_refs[0]?.artifact_key ?? gate.checked_artifact_refs[0]?.ref ?? gate.gate_key;
    return {
      id: `gate-${gate.gate_key}-${issue}-${index}`,
      package_id: handoff.package_id,
      video_project_id: handoff.video_project_id,
      effective_context_snapshot_id: handoff.effective_context_snapshot_id,
      gate_key: gate.gate_key,
      issue_code: issue,
      severity: gate.status === "BLOCK" ? "BLOCK" : "REVIEW_REQUIRED",
      target_artifact_type: String(target),
      target_artifact_ref: String(target),
      source_gate_run_id: null,
      source_gate_batch_id: null,
      status: "PENDING_PATCH",
      next_action_code: "NEEDS_PROPOSED_PATCH",
      human_readable_title: copy.title,
      human_readable_why: copy.why,
      human_readable_fix: copy.fix,
      section: copy.section,
      proposed_patch: null,
      created_at: handoff.created_at,
      updated_at: handoff.created_at
    };
  });
}

function failingGateItems(handoff: PackagingHandoff) {
  return handoff.packaging_gate_summary.gate_results.filter((gate) => gate.status === "BLOCK" || gate.status === "REVIEW_REQUIRED");
}

function issueCopy(issue: string) {
  const copies: Record<string, { title: string; why: string; fix: string; section: string }> = {
    HOOK_PROMISE_MISSING: {
      title: "Hook thiếu promise rõ ràng",
      why: "Người xem chưa biết video hứa trả lời điều gì.",
      fix: "Duyệt patch bổ sung promise và payoff location cho hook.",
      section: "Hook Review"
    },
    HOOK_VISUAL_MISSING: {
      title: "Hook thiếu visual 3 giây đầu",
      why: "Operator chưa có ý tưởng visual mở đầu khớp với script hook.",
      fix: "Duyệt patch visual hook không chạy render/provider.",
      section: "Hook Review"
    },
    SCRIPT_FORBIDDEN_STYLE_USED: {
      title: "Script dùng style bị cấm",
      why: "Narration script chứa wording/style nằm trong frozen channel/runtime contract.",
      fix: "Duyệt patch rewrite đúng câu vi phạm, giữ topic/claim/audience/evidence.",
      section: "Script Review"
    },
    TITLE_MISSING: {
      title: "Thiếu title upload",
      why: "Package chưa có title paste-ready cho YouTube.",
      fix: "Duyệt patch metadata có 3 title candidates và title khuyến nghị.",
      section: "Upload Copy"
    },
    SUBTITLE_REFS_MISSING: {
      title: "Chưa có subtitle refs",
      why: "Operator chưa biết subtitle là draft hay final.",
      fix: "Duyệt patch subtitle handoff hoặc mark subtitle_not_ready có lý do.",
      section: "Upload Copy / Subtitle"
    },
    THUMBNAIL_BRIEF_MISSING: {
      title: "Thiếu thumbnail brief",
      why: "Chưa có concept/overlay/subject để human tạo thumbnail.",
      fix: "Duyệt patch thumbnail brief.",
      section: "Thumbnail Handoff"
    },
    DESCRIPTION_MISSING: {
      title: "Thiếu description upload",
      why: "Package chưa có description ngắn gọn để paste sang YouTube.",
      fix: "Duyệt patch description không thêm CTA/resource giả.",
      section: "Upload Copy"
    },
    PUBLISH_WINDOW_MISSING: {
      title: "Thiếu publish window",
      why: "VCOS chưa có khung giờ publish khuyến nghị theo frozen context.",
      fix: "Duyệt package-level ManualPublishTimingOverride. Không mutate Channel Contract.",
      section: "Publish Timing"
    },
    TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM: {
      title: "Title đang hứa quá mức",
      why: "Title claim chưa được script/evidence trả đủ.",
      fix: "Duyệt title rewrite patch hoặc reject và request changes.",
      section: "Upload Copy"
    },
    DISCLOSURE_CONFLICT: {
      title: "Disclosure đang mâu thuẫn",
      why: "Metadata/upload copy không khớp rights/disclosure review.",
      fix: "Duyệt patch copy/disclosure wording.",
      section: "Disclosure / Upload Copy"
    },
    UNSUPPORTED_CTA: {
      title: "CTA chưa có bằng chứng hỗ trợ",
      why: "Copy đang nhắc asset/demo/checklist chưa tồn tại.",
      fix: "Duyệt patch xoá hoặc hạ claim CTA.",
      section: "Upload Copy"
    }
  };
  return copies[issue] ?? {
    title: "Gate packaging cần review",
    why: "Gate báo issue cần người vận hành xem trước upload.",
    fix: "Tạo proposed patch qua route domain phù hợp hoặc request changes.",
    section: "Packaging Review"
  };
}

function patchSummary(value: Record<string, unknown>) {
  const text = safeJson(value);
  return text.length > 220 ? `${text.slice(0, 220)}...` : text;
}

function missingPatchReason(item: PackagingReviewQueueItem) {
  const reasons: Record<string, string> = {
    ROUTE_NOT_AVAILABLE: "Cần proposed patch: chưa có route an toàn cho issue này.",
    LLM_PROPOSAL_DISABLED: "Cần proposed patch: LLM proposal đang tắt hoặc chưa được phép.",
    NEEDS_PROPOSED_PATCH: "Đang cần proposed patch"
  };
  return reasons[item.next_action_code] ?? "Đang cần proposed patch";
}

function shortValue(value?: string | null) {
  return value ? value.slice(0, 12) : "Chưa có";
}

function safeJson(value: unknown) {
  if (!value) return "Chưa có dữ liệu";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function formatDate(value?: string | null) {
  if (!value) return "Chưa có dữ liệu";
  return new Date(value).toLocaleString("vi-VN");
}

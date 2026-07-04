"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clipboard, Clock, FileText, ImageIcon, ShieldCheck, UploadCloud, type LucideIcon } from "lucide-react";

import { EmptyStateCard, MetricSummaryCard, PageHeader, TechnicalAppendix } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { createUploadTaskFromPackage, getVideoPackageReview, queryKeys } from "@/lib/api";
import type { PackagingGateResult, PackagingHandoff } from "@/lib/types";

export function PackageReviewView({ packageId }: { packageId: string }) {
  const queryClient = useQueryClient();
  const [taskMessage, setTaskMessage] = useState<string | null>(null);
  const query = useQuery({
    queryKey: queryKeys.videoPackageReview(packageId),
    queryFn: () => getVideoPackageReview(packageId)
  });
  const taskMutation = useMutation({
    mutationFn: () => createUploadTaskFromPackage(packageId),
    onSuccess: async (task) => {
      setTaskMessage(`Task upload thủ công đã sẵn sàng: ${task.id.slice(0, 8)}. ${task.next_action}`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.videoPackageReview(packageId) });
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
      <PageHeader
        title="Handoff upload thủ công"
        subtitle="Review hook, copy, thumbnail, thời điểm publish khuyến nghị và gate packaging trước khi upload ngoài VCOS."
        breadcrumbs={[{ label: "Gói publish", href: "/publishing" }, { label: packageId.slice(0, 8) }]}
        primaryAction={
          <Button variant="primary" onClick={() => taskMutation.mutate()} disabled={taskMutation.isPending}>
            <UploadCloud size={16} aria-hidden="true" /> Tạo task upload thủ công
          </Button>
        }
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={ShieldCheck} label="Trạng thái package" value={<StatusBadge value={handoff.package_status} />} hint="Human final approval vẫn bắt buộc." />
        <MetricSummaryCard icon={Check} label="Gate packaging" value={<StatusBadge value={handoff.packaging_gate_summary.overall_status} />} hint={handoff.packaging_gate_summary.next_action_vi} />
        <MetricSummaryCard icon={Clock} label="Publish thủ công" value={handoff.publish_timing_recommendation.channel_timezone ?? "Chưa có timezone"} hint="VCOS chỉ nhắc thời điểm, không schedule." />
        <MetricSummaryCard icon={UploadCloud} label="Paste-back" value={String(handoff.manual_upload.task_status ?? "Chưa tạo task")} hint={String(handoff.manual_upload.next_action_vi ?? "Nhập URL/video_id sau khi upload thủ công.")} />
      </div>
      {taskMessage ? <div className="rounded-md border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-100">{taskMessage}</div> : null}
      {taskMutation.isError ? <div className="rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">{taskMutation.error.message}</div> : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <HookReviewPanel handoff={handoff} />
        <UploadCopyPanel handoff={handoff} />
        <ThumbnailPanel handoff={handoff} />
        <PublishTimingPanel handoff={handoff} />
      </div>
      <PackagingGateSummary handoff={handoff} />
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

function PackagingGateSummary({ handoff }: { handoff: PackagingHandoff }) {
  return (
    <Panel>
      <PanelHeading icon={ShieldCheck} title="Tóm tắt gate packaging" status={handoff.packaging_gate_summary.overall_status} />
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
    </Panel>
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

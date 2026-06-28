"use client";

import { useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, CheckCircle2, ClipboardEdit, Database, ExternalLink, Gauge, ListChecks, RefreshCw, UploadCloud, Video } from "lucide-react";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import {
  activateChannel,
  backfillUploadedVideo,
  getChannelPublishLedger,
  getChannelUploadedVideos,
  getChannelUploadTasks,
  getChannelWorkspace,
  queryKeys,
  startUploadTask,
  verifyUploadedVideo
} from "@/lib/api";
import type { BackfillUploadedVideoInput, HumanUploadTask } from "@/lib/types";

export function ChannelWorkspaceView({ channelId }: { channelId: string }) {
  const queryClient = useQueryClient();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [backfillForm, setBackfillForm] = useState<BackfillUploadedVideoInput>({
    youtube_url_or_video_id: "",
    actual_visibility: "UNKNOWN",
    thumbnail_uploaded: false,
    subtitles_uploaded: false,
    description_modified_from_package: false
  });
  const [backfillResult, setBackfillResult] = useState<string | null>(null);
  const query = useQuery({ queryKey: queryKeys.channelWorkspace(channelId), queryFn: () => getChannelWorkspace(channelId) });
  const ledgerQuery = useQuery({ queryKey: queryKeys.channelPublishLedger(channelId), queryFn: () => getChannelPublishLedger(channelId) });
  const tasksQuery = useQuery({ queryKey: queryKeys.channelUploadTasks(channelId), queryFn: () => getChannelUploadTasks(channelId) });
  const uploadedQuery = useQuery({ queryKey: queryKeys.channelUploadedVideos(channelId), queryFn: () => getChannelUploadedVideos(channelId) });
  const invalidateLedger = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.channelPublishLedger(channelId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.channelUploadTasks(channelId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.channelUploadedVideos(channelId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.channelWorkspace(channelId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.uploadedVideos });
  };
  const startMutation = useMutation({
    mutationFn: startUploadTask,
    onSuccess: (task) => {
      setSelectedTaskId(task.id);
      invalidateLedger();
    }
  });
  const backfillMutation = useMutation({
    mutationFn: ({ taskId, input }: { taskId: string; input: BackfillUploadedVideoInput }) => backfillUploadedVideo(taskId, input),
    onSuccess: (result) => {
      setBackfillResult(`Đã parse video_id: ${result.parsed_video_id}. ${result.next_action}`);
      setSelectedTaskId(result.task.id);
      invalidateLedger();
    }
  });
  const verifyMutation = useMutation({
    mutationFn: verifyUploadedVideo,
    onSuccess: invalidateLedger
  });
  const [activateError, setActivateError] = useState<string | null>(null);
  const activateMutation = useMutation({
    mutationFn: () => activateChannel(channelId),
    onSuccess: async () => {
      setActivateError(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.channelWorkspace(channelId) });
    },
    onError: (error: Error) => {
      setActivateError(error.message || "Kích hoạt kênh thất bại.");
    }
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải không gian kênh" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải không gian kênh" /></div>;

  const workspace = query.data;
  const ledger = ledgerQuery.data ?? workspace.publish_ledger;
  const contractReview = workspace.health_summary.contract_review as {
    contract_status?: string;
    label?: string;
    latest_snapshot_id?: string | null;
    active_snapshot_id?: string | null;
    snapshot_version?: number;
    missing_fields?: string[];
    contradiction_reasons?: string[];
    market_locale?: Record<string, unknown>;
    next_action?: string;
  } | undefined;
  const uploadTasks = tasksQuery.data?.tasks ?? [];
  const tasksNeedingUpload = uploadTasks.filter((task) => !task.actual_uploaded_video_id && task.status !== "CANCELLED");
  const uploadedVideos = uploadedQuery.data?.uploaded_videos ?? [];
  const selectedTask = uploadTasks.find((task) => task.id === selectedTaskId) ?? tasksNeedingUpload[0] ?? uploadTasks[0];

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title={workspace.channel.name}
        subtitle={String(workspace.health_summary.next_action ?? workspace.lifecycle.next_action)}
        breadcrumbs={[{ label: "Kênh", href: "/channels" }, { label: workspace.channel.name }]}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={Activity} label="Vòng đời kênh" value={<StatusValue value={workspace.lifecycle.lifecycle_state} />} hint="Vòng đời do người vận hành quyết định." />
        <MetricSummaryCard icon={Gauge} label="Sức khỏe" value={<StatusValue value={workspace.lifecycle.health_status} />} hint="Trạng thái sức khỏe chỉ cảnh báo, không tự đổi vòng đời." />
        <MetricSummaryCard icon={CheckCircle2} label="Hồ sơ kênh" value={contractReview?.label ?? "Cần bổ sung hồ sơ"} hint="Channel Contract quyết định khả năng kích hoạt." />
        <MetricSummaryCard icon={ListChecks} label="Việc đang chờ" value={workspace.approvals.length} hint="Bao gồm bài học, gói publish, vận hành và phục hồi." />
        <MetricSummaryCard icon={Database} label="Tệp Drive" value={String(workspace.media_storage.cloud_media_count ?? 0)} hint="Chỉ mở file qua CTA Google Drive." />
        <MetricSummaryCard icon={UploadCloud} label="Cần upload thủ công" value={ledger?.need_upload_count ?? 0} hint="Gói đã duyệt, chưa có UploadedVideo." />
        <MetricSummaryCard icon={ClipboardEdit} label="Chờ nhập video_id" value={ledger?.waiting_backfill_count ?? 0} hint="Upload xong thì paste URL/video_id vào VCOS." />
        <MetricSummaryCard icon={Video} label="Đã upload" value={ledger?.uploaded_count ?? 0} hint="VCOS đã ghi nhận video YouTube." />
        <MetricSummaryCard icon={CheckCircle2} label="Đã xác minh" value={ledger?.verified_count ?? 0} hint="Xác minh qua YouTube read-only khi đã kết nối." />
      </div>
      <Tabs.Root defaultValue="overview">
        <Tabs.List className="flex flex-wrap gap-2">
          {[
            ["overview", "Tổng quan"],
            ["projects", "Dự án"],
            ["approvals", "Hàng chờ"],
            ["publishing", "Gói publish"],
            ["need-upload", "Cần upload"],
            ["uploaded", "Đã upload"],
            ["backfill", "Nhập kết quả upload"],
            ["analytics", "Dữ liệu phân tích"],
            ["learning", "Bài học"],
            ["profile-policy", "Hồ sơ & chính sách kênh"],
            ["media", "Tệp Drive"],
            ["provider-health", "Vận hành"]
          ].map(([tab, label]) => (
            <Tabs.Trigger key={tab} value={tab} className="rounded-md border border-border px-3 py-2 text-sm data-[state=active]:border-primary data-[state=active]:text-primary">
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="overview" className="mt-5">
          <Panel>
            <h2 className="text-base font-semibold">Vòng đời kênh</h2>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              <StatusBadge value={workspace.lifecycle.lifecycle_state} />
              <StatusBadge value={workspace.lifecycle.health_status} />
              <StatusBadge value={workspace.lifecycle.daily_generation_allowed ? "DAILY_ALLOWED" : "DAILY_BLOCKED"} />
            </div>
            <p className="mt-4 text-sm text-muted-foreground">{workspace.lifecycle.next_action}</p>
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="projects" className="mt-5">
          {workspace.projects.length ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {workspace.projects.map((project) => (
                <Panel key={String(project.id)}>
                  <h2 className="text-base font-semibold">{String(project.title)}</h2>
                  <p className="mt-2 text-sm text-muted-foreground">{String(project.next_action)}</p>
                  <div className="mt-3"><StatusBadge value={String(project.current_stage)} /></div>
                </Panel>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="Chưa có dự án" description="Luồng tạo hằng ngày chỉ tạo việc mới khi vòng đời là Đang hoạt động. Kiểm tra snapshot chính sách và trạng thái kênh trước khi chạy." />
          )}
        </Tabs.Content>
        <Tabs.Content value="approvals" className="mt-5">
          {workspace.approvals.length ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {workspace.approvals.map((item) => <ApprovalCard key={item.queue_item_id ?? item.entity_id ?? item.operator_summary} item={item} />)}
            </div>
          ) : (
            <EmptyStateCard title="Không có blocker cần duyệt" description="Kênh này hiện chưa có bài học, gói publish hoặc thao tác ops cần xử lý. Khi có việc mới, next action sẽ hiện ở đây." />
          )}
        </Tabs.Content>
        <Tabs.Content value="media" className="mt-5">
          <Panel>
            <h2 className="text-base font-semibold">Tệp Drive</h2>
            <p className="mt-2 text-sm text-muted-foreground">Google Drive chỉ là nơi lưu file. Dashboard chỉ dùng nút mở Drive đã xác minh và không bao giờ hiện đường dẫn local.</p>
            <div className="mt-4"><StatusBadge value={String(workspace.media_storage.storage_state ?? workspace.health_summary.storage_state)} /></div>
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="publishing" className="mt-5">
          <EmptyStateCard title="Gói publish" description="Mở hàng chờ gói publish để lấy CTA Google Drive, sao chép thông tin mô tả và nhập paste-back sau khi upload thủ công. VCOS chỉ ghi nhận và xác minh." actions={[{ label: "Đi tới gói publish", href: "/publishing" }]} />
        </Tabs.Content>
        <Tabs.Content value="need-upload" className="mt-5">
          <UploadTasksTable
            tasks={tasksNeedingUpload}
            onStart={(task) => startMutation.mutate(task.id)}
            onSelect={(task) => setSelectedTaskId(task.id)}
            busyTaskId={startMutation.variables ?? null}
          />
        </Tabs.Content>
        <Tabs.Content value="uploaded" className="mt-5">
          {uploadedVideos.length ? (
            <Panel className="overflow-x-auto p-0">
              <table className="w-full min-w-[1120px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    {["YouTube video_id", "YouTube URL", "Tiêu đề thực tế", "Visibility", "Uploaded/published", "Xác minh", "Analytics", "Last sync", ""].map((header) => (
                      <th key={header} className="px-4 py-3 font-medium">{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {uploadedVideos.map((video) => (
                    <tr key={video.id} className="border-b border-border/60">
                      <td className="px-4 py-3 font-mono text-xs">{video.external_video_id}</td>
                      <td className="px-4 py-3">
                        <a className="inline-flex items-center gap-2 text-primary hover:underline" href={video.external_url} target="_blank" rel="noreferrer">
                          Mở trên YouTube <ExternalLink size={14} aria-hidden="true" />
                        </a>
                      </td>
                      <td className="px-4 py-3">{video.actual_title ?? "Chưa nhập"}</td>
                      <td className="px-4 py-3"><StatusBadge value={video.actual_visibility} /></td>
                      <td className="px-4 py-3">{formatDate(video.actual_publish_time ?? video.actual_upload_time ?? video.created_at)}</td>
                      <td className="px-4 py-3"><StatusBadge value={video.verification_status} /></td>
                      <td className="px-4 py-3"><StatusBadge value={video.analytics_sync_status} /></td>
                      <td className="px-4 py-3">{video.last_analytics_sync_at ? formatDate(video.last_analytics_sync_at) : "Chưa sync"}</td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-2">
                          <Button asChild><a href={video.external_url} target="_blank" rel="noreferrer">Mở YouTube</a></Button>
                          <Button onClick={() => verifyMutation.mutate(video.id)} disabled={verifyMutation.isPending}>
                            <RefreshCw size={15} aria-hidden="true" /> Xác minh lại
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          ) : (
            <EmptyStateCard
              title="Chưa có video đã upload"
              description="Sau khi bạn publish video thủ công lên YouTube, hãy quay lại VCOS để nhập URL/video_id. VCOS sẽ dùng thông tin đó để theo dõi views, likes, comments, analytics và diagnostic."
              actions={[{ label: "Đi tới gói publish", href: "/publishing", variant: "primary" }]}
            />
          )}
        </Tabs.Content>
        <Tabs.Content value="backfill" className="mt-5">
          <Panel>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold">Nhập kết quả upload thủ công</h2>
                <p className="mt-2 text-sm text-muted-foreground">VCOS không upload, không publish và không schedule trên YouTube. VCOS chỉ ghi nhận và xác minh.</p>
              </div>
              {selectedTask ? <StatusBadge value={selectedTask.status} /> : null}
            </div>
            {selectedTask ? (
              <form
                className="mt-5 grid gap-4 md:grid-cols-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  setBackfillResult(null);
                  backfillMutation.mutate({ taskId: selectedTask.id, input: backfillForm });
                }}
              >
                <label className="space-y-2 text-sm">
                  <span className="text-muted-foreground">YouTube URL hoặc video_id</span>
                  <input
                    className="w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground"
                    value={backfillForm.youtube_url_or_video_id}
                    onChange={(event) => setBackfillForm((current) => ({ ...current, youtube_url_or_video_id: event.target.value }))}
                    required
                  />
                </label>
                <label className="space-y-2 text-sm">
                  <span className="text-muted-foreground">Tiêu đề thực tế</span>
                  <input
                    className="w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground"
                    value={backfillForm.actual_title ?? ""}
                    onChange={(event) => setBackfillForm((current) => ({ ...current, actual_title: event.target.value || null }))}
                  />
                </label>
                <label className="space-y-2 text-sm">
                  <span className="text-muted-foreground">Visibility</span>
                  <select
                    className="w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground"
                    value={backfillForm.actual_visibility ?? "UNKNOWN"}
                    onChange={(event) => setBackfillForm((current) => ({ ...current, actual_visibility: event.target.value }))}
                  >
                    {["PUBLIC", "UNLISTED", "PRIVATE", "SCHEDULED", "UNKNOWN"].map((value) => <option key={value} value={value}>{visibilityLabel(value)}</option>)}
                  </select>
                </label>
                <label className="space-y-2 text-sm">
                  <span className="text-muted-foreground">Publish/schedule time</span>
                  <input
                    className="w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground"
                    type="datetime-local"
                    onChange={(event) => setBackfillForm((current) => ({ ...current, actual_publish_time: event.target.value ? new Date(event.target.value).toISOString() : null }))}
                  />
                </label>
                <label className="space-y-2 text-sm">
                  <span className="text-muted-foreground">Playlist ID</span>
                  <input
                    className="w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground"
                    value={backfillForm.playlist_id ?? ""}
                    onChange={(event) => setBackfillForm((current) => ({ ...current, playlist_id: event.target.value || null }))}
                  />
                </label>
                <label className="space-y-2 text-sm md:col-span-2">
                  <span className="text-muted-foreground">Ghi chú operator</span>
                  <textarea
                    className="min-h-24 w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground"
                    value={backfillForm.operator_note ?? ""}
                    onChange={(event) => setBackfillForm((current) => ({ ...current, operator_note: event.target.value || null }))}
                  />
                </label>
                <div className="flex flex-wrap gap-4 text-sm md:col-span-2">
                  {[
                    ["thumbnail_uploaded", "Thumbnail đã upload?"],
                    ["subtitles_uploaded", "Subtitle đã upload?"],
                    ["description_modified_from_package", "Description có chỉnh khác package không?"]
                  ].map(([key, label]) => (
                    <label key={key} className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={Boolean(backfillForm[key as keyof BackfillUploadedVideoInput])}
                        onChange={(event) => setBackfillForm((current) => ({ ...current, [key]: event.target.checked }))}
                      />
                      {label}
                    </label>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-3 md:col-span-2">
                  <Button variant="primary" type="submit" disabled={backfillMutation.isPending}>Lưu video đã upload</Button>
                  <span className="text-sm text-muted-foreground">{selectedTask.title_snapshot}</span>
                </div>
                {backfillResult ? <p className="text-sm text-primary md:col-span-2">{backfillResult}</p> : null}
                {backfillMutation.isError ? <p className="text-sm text-destructive md:col-span-2">{backfillMutation.error.message}</p> : null}
              </form>
            ) : (
              <EmptyStateCard title="Chưa có task upload" description="Hãy tạo gói upload từ package đã duyệt trước. Sau khi upload thủ công lên YouTube, form paste-back sẽ hiện ở đây." />
            )}
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="analytics" className="mt-5">
          <EmptyStateCard title="Analytics" description="Chi tiết video đã upload sẽ hiển thị thống kê YouTube công khai và analytics chủ sở hữu; metric chưa có dữ liệu không phải bằng 0." actions={[{ label: "Xem video đã upload", href: "/uploaded-videos" }]} />
        </Tabs.Content>
        <Tabs.Content value="learning" className="mt-5">
          <EmptyStateCard title="Bài học chờ duyệt" description="Bài học cần người duyệt bằng chứng trước khi trở thành mục playbook. VCOS không tự đổi cấu hình kênh." actions={[{ label: "Xem bài học", href: "/learning" }]} />
        </Tabs.Content>
        <Tabs.Content value="profile-policy" className="mt-5">
          <Panel>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold">Hồ sơ & chính sách kênh</h2>
                <p className="mt-2 text-sm text-muted-foreground">Người vận hành chỉnh sửa sẽ tạo phiên bản hồ sơ và snapshot chính sách mới. Dự án cũ vẫn giữ snapshot đã gắn trước đó.</p>
              </div>
              <StatusBadge value={contractReview?.contract_status ?? "MISSING"} />
            </div>
            <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <InfoTile label="Policy snapshot" value={contractReview?.latest_snapshot_id ?? "Chưa có snapshot"} />
              <InfoTile label="Phiên bản snapshot" value={contractReview?.snapshot_version ? String(contractReview.snapshot_version) : "Chưa có"} />
              <InfoTile label="Publish policy" value="Không tự publish / Human handoff only" />
              <InfoTile label="Market" value={String(contractReview?.market_locale?.primary_market ?? "Chưa nhập")} />
              <InfoTile label="Locale" value={String(contractReview?.market_locale?.audience_locale ?? "Chưa nhập")} />
              <InfoTile label="Timezone" value={String(contractReview?.market_locale?.timezone ?? "Chưa nhập")} />
            </div>
            {contractReview?.missing_fields?.length ? (
              <div className="mt-5 rounded-md border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">
                Thiếu: {contractReview.missing_fields.join(", ")}
              </div>
            ) : null}
            {contractReview?.contradiction_reasons?.length ? (
              <div className="mt-5 rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">
                Mâu thuẫn: {contractReview.contradiction_reasons.join(", ")}
              </div>
            ) : null}
            {(() => {
              const isDraft = ["DRAFT", "READY"].includes(workspace.lifecycle.lifecycle_state);
              const isComplete = contractReview?.contract_status === "COMPLETE";
              const hasSnap = Boolean(contractReview?.latest_snapshot_id);
              const canActivate = isDraft && isComplete && hasSnap;
              if (canActivate) {
                return (
                  <div className="mt-5 flex flex-col gap-3">
                    <Button
                      variant="primary"
                      onClick={() => activateMutation.mutate()}
                      disabled={activateMutation.isPending}
                    >
                      {activateMutation.isPending ? "Đang kích hoạt..." : "Kích hoạt kênh"}
                    </Button>
                    <p className="text-xs text-muted-foreground">Người vận hành quyết định kích hoạt. VCOS không tự publish/upload/reupload.</p>
                  </div>
                );
              }
              if (isDraft && !isComplete) {
                return (
                  <div className="mt-5 flex flex-col gap-3">
                    <Button variant="primary" disabled>Bổ sung hồ sơ kênh</Button>
                    <p className="text-xs text-muted-foreground">{`Hợp đồng kênh chưa hoàn chỉnh (${contractReview?.contract_status ?? "MISSING"}). Cần bổ sung trước khi kích hoạt.`}</p>
                  </div>
                );
              }
              if (isDraft && !hasSnap) {
                return (
                  <div className="mt-5 flex flex-col gap-3">
                    <Button variant="primary" disabled>Kích hoạt kênh</Button>
                    <p className="text-xs text-muted-foreground">Chưa có policy snapshot. Cần compile snapshot trước khi kích hoạt.</p>
                  </div>
                );
              }
              return <p className="mt-5 text-sm text-muted-foreground">{contractReview?.next_action ?? "Bổ sung hồ sơ kênh và compile policy snapshot."}</p>;
            })()}
            {activateError ? (
              <div className="mt-3 rounded-md border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">
                {activateError}
              </div>
            ) : null}
            {activateMutation.isSuccess && workspace.lifecycle.lifecycle_state === "ACTIVE" ? (
              <div className="mt-3 rounded-md border border-green-400/30 bg-green-400/10 p-3 text-sm text-green-100">
                Kênh đã được kích hoạt. Các project mới sẽ dùng snapshot hiện tại.
              </div>
            ) : null}
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="provider-health" className="mt-5">
          <Panel>
            <h2 className="text-base font-semibold">Vận hành</h2>
            <p className="mt-2 text-sm text-muted-foreground">Trạng thái nhà cung cấp là dữ liệu đọc từ backend đã được bảo vệ. Khi mở trang, dashboard không gọi nhà cung cấp thật.</p>
          </Panel>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}

function StatusValue({ value }: { value: string }) {
  return <span className="inline-flex"><StatusBadge value={value} /></span>;
}

function InfoTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
      <div className="text-muted-foreground">{label}</div>
      <div className="mt-1 break-words font-medium">{value}</div>
    </div>
  );
}

function UploadTasksTable({
  tasks,
  onStart,
  onSelect,
  busyTaskId
}: {
  tasks: HumanUploadTask[];
  onStart: (task: HumanUploadTask) => void;
  onSelect: (task: HumanUploadTask) => void;
  busyTaskId: string | null;
}) {
  if (!tasks.length) {
    return (
      <EmptyStateCard
        title="Chưa có video cần upload"
        description="Khi package đã được duyệt cho upload thủ công, task sẽ xuất hiện ở đây. VCOS chỉ chuẩn bị gói upload và ghi nhận kết quả paste-back."
        actions={[{ label: "Đi tới gói publish", href: "/publishing", variant: "primary" }]}
      />
    );
  }
  return (
    <Panel className="overflow-x-auto p-0">
      <table className="w-full min-w-[1080px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            {["Video title", "Project", "Package version", "Destination", "Status", "Created at", "Assets/checklist", ""].map((header) => (
              <th key={header} className="px-4 py-3 font-medium">{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={task.id} className="border-b border-border/60">
              <td className="px-4 py-3 font-medium">{task.title_snapshot}</td>
              <td className="px-4 py-3 font-mono text-xs">{shortId(task.video_project_id)}</td>
              <td className="px-4 py-3 font-mono text-xs">{shortId(task.first_scripted_video_package_id ?? task.publish_package_id)}</td>
              <td className="px-4 py-3"><StatusBadge value={task.destination} /></td>
              <td className="px-4 py-3"><StatusBadge value={task.status} /></td>
              <td className="px-4 py-3">{formatDate(task.created_at)}</td>
              <td className="px-4 py-3">{task.required_assets.length} assets / {task.checklist.length} checklist</td>
              <td className="px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => onSelect(task)}>Mở gói upload</Button>
                  <Button onClick={() => onStart(task)} disabled={task.status !== "READY_FOR_HUMAN_UPLOAD" || busyTaskId === task.id}>
                    Đánh dấu đã upload
                  </Button>
                  <Button variant="primary" onClick={() => onSelect(task)}>Nhập video_id</Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function shortId(value: string | null | undefined) {
  return value ? value.slice(0, 8) : "Chưa gắn";
}

function formatDate(value: string | null | undefined) {
  if (!value) return "Chưa có dữ liệu";
  return new Date(value).toLocaleString("vi-VN");
}

function visibilityLabel(value: string) {
  return {
    PUBLIC: "Công khai",
    UNLISTED: "Không công khai",
    PRIVATE: "Riêng tư",
    SCHEDULED: "Đã lên lịch trên YouTube",
    UNKNOWN: "Chưa rõ"
  }[value] ?? "Chưa rõ";
}

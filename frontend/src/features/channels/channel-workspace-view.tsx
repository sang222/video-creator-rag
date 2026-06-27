"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { Activity, Database, Gauge, ListChecks } from "lucide-react";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getChannelWorkspace, queryKeys } from "@/lib/api";

export function ChannelWorkspaceView({ channelId }: { channelId: string }) {
  const query = useQuery({ queryKey: queryKeys.channelWorkspace(channelId), queryFn: () => getChannelWorkspace(channelId) });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải không gian kênh" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải không gian kênh" /></div>;

  const workspace = query.data;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title={workspace.channel.name}
        subtitle={String(workspace.health_summary.next_action ?? workspace.lifecycle.next_action)}
        breadcrumbs={[{ label: "Không gian kênh", href: "/channels" }, { label: workspace.channel.name }]}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={Activity} label="Vòng đời kênh" value={<StatusValue value={workspace.lifecycle.lifecycle_state} />} hint="Vòng đời do người vận hành quyết định." />
        <MetricSummaryCard icon={Gauge} label="Sức khỏe" value={<StatusValue value={workspace.lifecycle.health_status} />} hint="Trạng thái sức khỏe chỉ cảnh báo, không tự đổi vòng đời." />
        <MetricSummaryCard icon={ListChecks} label="Việc đang chờ" value={workspace.approvals.length} hint="Bao gồm bài học, publish, ops và phục hồi." />
        <MetricSummaryCard icon={Database} label="Tệp Google Drive" value={String(workspace.media_storage.cloud_media_count ?? 0)} hint="Chỉ mở file qua CTA Google Drive." />
      </div>
      <Tabs.Root defaultValue="overview">
        <Tabs.List className="flex flex-wrap gap-2">
          {[
            ["overview", "Tổng quan"],
            ["projects", "Dự án"],
            ["approvals", "Hàng chờ"],
            ["publishing", "Gói publish"],
            ["analytics", "Dữ liệu phân tích"],
            ["learning", "Bài học"],
            ["profile-policy", "Hồ sơ & chính sách kênh"],
            ["media", "Tệp Google Drive"],
            ["provider-health", "Trạng thái nhà cung cấp"]
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
            <EmptyStateCard title="Chưa có dự án" description="Daily generation chỉ tạo việc mới khi vòng đời là Đang hoạt động. Kiểm tra policy snapshot và trạng thái kênh trước khi chạy." />
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
            <h2 className="text-base font-semibold">Tệp trên Google Drive</h2>
            <p className="mt-2 text-sm text-muted-foreground">Google Drive chỉ là nơi lưu file. Dashboard chỉ dùng nút mở Drive đã xác minh và không bao giờ hiện đường dẫn local.</p>
            <div className="mt-4"><StatusBadge value={String(workspace.media_storage.storage_state ?? workspace.health_summary.storage_state)} /></div>
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="publishing" className="mt-5">
          <EmptyStateCard title="Gói publish" description="Mở hàng chờ gói publish để lấy CTA Google Drive, copy metadata và nhập paste-back sau khi upload thủ công." actions={[{ label: "Đi tới gói publish", href: "/publishing" }]} />
        </Tabs.Content>
        <Tabs.Content value="analytics" className="mt-5">
          <EmptyStateCard title="Analytics" description="Chi tiết video đã upload sẽ hiển thị public YouTube stats và owner analytics; metric chưa có dữ liệu không phải bằng 0." actions={[{ label: "Xem video đã upload", href: "/uploaded-videos" }]} />
        </Tabs.Content>
        <Tabs.Content value="learning" className="mt-5">
          <EmptyStateCard title="Bài học chờ duyệt" description="Learning candidate cần người duyệt bằng chứng trước khi trở thành playbook entry. VCOS không tự đổi cấu hình kênh." actions={[{ label: "Xem bài học", href: "/learning" }]} />
        </Tabs.Content>
        <Tabs.Content value="profile-policy" className="mt-5">
          <EmptyStateCard title="Hồ sơ & chính sách kênh" description="Người vận hành chỉnh sửa sẽ tạo phiên bản hồ sơ và snapshot chính sách mới. Dự án cũ vẫn giữ snapshot đã gắn trước đó." />
        </Tabs.Content>
        <Tabs.Content value="provider-health" className="mt-5">
          <Panel>
            <h2 className="text-base font-semibold">Trạng thái nhà cung cấp</h2>
            <p className="mt-2 text-sm text-muted-foreground">Trạng thái provider là dữ liệu đọc từ backend đã guard. Dashboard load không gọi provider thật.</p>
          </Panel>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}

function StatusValue({ value }: { value: string }) {
  return <span className="inline-flex"><StatusBadge value={value} /></span>;
}

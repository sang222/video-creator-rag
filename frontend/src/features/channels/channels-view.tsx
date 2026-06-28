"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { Boxes, CheckCircle2, ClipboardEdit, PauseCircle, ShieldAlert, UploadCloud, Video } from "lucide-react";

import { EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { getChannels, queryKeys } from "@/lib/api";
import type { ChannelSummary } from "@/lib/types";

const columnHelper = createColumnHelper<ChannelSummary>();
const columns = [
  columnHelper.accessor("name", {
    header: "Kênh",
    cell: (info) => <span className="font-medium">{info.getValue()}</span>
  }),
  columnHelper.accessor("lifecycle_state", {
    header: "Vòng đời",
    cell: (info) => <StatusBadge value={info.getValue()} />
  }),
  columnHelper.accessor("health_status", {
    header: "Sức khỏe",
    cell: (info) => <StatusBadge value={info.getValue()} />
  }),
  columnHelper.display({
    id: "need_upload",
    header: "Cần upload",
    cell: ({ row }) => row.original.upload_counts?.need_upload_count ?? 0
  }),
  columnHelper.display({
    id: "waiting_backfill",
    header: "Chờ nhập video_id",
    cell: ({ row }) => row.original.upload_counts?.waiting_backfill_count ?? 0
  }),
  columnHelper.display({
    id: "uploaded",
    header: "Đã upload",
    cell: ({ row }) => row.original.upload_counts?.uploaded_count ?? 0
  }),
  columnHelper.display({
    id: "waiting_verification",
    header: "Chờ xác minh YouTube",
    cell: ({ row }) => row.original.upload_counts?.waiting_verification_count ?? 0
  }),
  columnHelper.accessor("next_action", {
    header: "Việc tiếp theo"
  }),
  columnHelper.display({
    id: "open",
    header: "",
    cell: ({ row }) => (
      <Button asChild>
        <Link href={`/channels/${row.original.id}`}>Mở</Link>
      </Button>
    )
  })
];

export function ChannelsView() {
  const query = useQuery({ queryKey: queryKeys.channels, queryFn: getChannels });
  const table = useReactTable({ data: query.data ?? [], columns, getCoreRowModel: getCoreRowModel() });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải không gian kênh" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải không gian kênh" /></div>;

  const activeCount = query.data.filter((channel) => channel.lifecycle_state === "ACTIVE").length;
  const pausedCount = query.data.filter((channel) => ["PAUSED", "DEACTIVATED", "ARCHIVED"].includes(channel.lifecycle_state)).length;
  const reviewCount = query.data.filter((channel) => ["WATCHLIST", "NEEDS_HUMAN_REVIEW", "NO_VIEW", "LOW_VIEW"].includes(channel.health_status)).length;
  const needUploadCount = query.data.reduce((sum, channel) => sum + (channel.upload_counts?.need_upload_count ?? 0), 0);
  const waitingBackfillCount = query.data.reduce((sum, channel) => sum + (channel.upload_counts?.waiting_backfill_count ?? 0), 0);
  const uploadedCount = query.data.reduce((sum, channel) => sum + (channel.upload_counts?.uploaded_count ?? 0), 0);

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Kênh"
        subtitle="Người vận hành quyết định vòng đời kênh; trạng thái sức khỏe chỉ dùng để quan sát và cảnh báo."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Kênh" }]}
        primaryAction={<Button asChild variant="primary"><Link href="/channels/new">Tạo kênh</Link></Button>}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={Boxes} label="Tổng số kênh" value={query.data.length} hint="Mỗi kênh giữ snapshot chính sách riêng." />
        <MetricSummaryCard icon={CheckCircle2} label="Kênh đang hoạt động" value={activeCount} hint="Luồng tạo hằng ngày chỉ chạy khi vòng đời là Đang hoạt động." />
        <MetricSummaryCard icon={PauseCircle} label="Kênh tạm dừng/ngừng" value={pausedCount} hint="Người vận hành có thể bật lại khi đủ điều kiện." />
        <MetricSummaryCard icon={ShieldAlert} label="Kênh cần xem lại" value={reviewCount} hint="Mở kênh để xem blocker và việc tiếp theo." />
        <MetricSummaryCard icon={UploadCloud} label="Cần upload thủ công" value={needUploadCount} hint="Gói đã duyệt, chưa có UploadedVideo." />
        <MetricSummaryCard icon={ClipboardEdit} label="Chờ nhập video_id" value={waitingBackfillCount} hint="Đã bắt đầu upload thủ công, cần paste-back URL/video_id." />
        <MetricSummaryCard icon={Video} label="Đã upload" value={uploadedCount} hint="Đã có record UploadedVideo trong VCOS." />
      </div>
      {query.data.length ? (
        <Panel className="overflow-x-auto p-0">
          <table className="w-full min-w-[1120px] border-collapse text-sm">
            <thead>
                {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-border">
                  {headerGroup.headers.map((header) => (
                    <th key={header.id} className="px-4 py-3 text-left font-medium text-muted-foreground">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr key={row.id} className="border-b border-border/60">
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-4 py-3 align-top">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : (
        <EmptyStateCard
          title="Chưa có kênh"
          description="Tạo kênh đầu tiên, sinh snapshot chính sách, rồi người vận hành bật kênh trước khi daily generation được phép chạy."
          actions={[{ label: "Tạo kênh", href: "/channels/new", variant: "primary" }]}
        />
      )}
    </div>
  );
}

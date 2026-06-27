"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CirclePlay, MessageCircle, ThumbsUp, Video } from "lucide-react";

import { EmptyStateCard, MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { getUploadedVideos, queryKeys } from "@/lib/api";

export function UploadedVideosView() {
  const query = useQuery({ queryKey: queryKeys.uploadedVideos, queryFn: getUploadedVideos });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải video đã upload" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải video đã upload" /></div>;

  const totals = query.data.reduce(
    (acc, video) => ({
      views: acc.views + Number(video.metrics.views ?? 0),
      likes: acc.likes + Number(video.metrics.likes ?? 0),
      comments: acc.comments + Number(video.metrics.comments ?? 0)
    }),
    { views: 0, likes: 0, comments: 0 }
  );

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Video đã upload"
        subtitle="Theo dõi video YouTube đã được publish thủ công. Metric chưa có dữ liệu không phải bằng 0."
        breadcrumbs={[{ label: "Trung tâm điều hành", href: "/" }, { label: "Video đã upload" }]}
        primaryAction={<Button asChild variant="primary"><Link href="/publishing">Đi tới gói publish</Link></Button>}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={Video} label="Video đã nhập paste-back" value={query.data.length} hint="UploadedVideo chỉ được tạo sau khi human nhập URL/video_id." />
        <MetricSummaryCard icon={CirclePlay} label="Views đã ghi nhận" value={query.data.length ? totals.views : "Chưa có dữ liệu"} hint="Không suy diễn performance theo quốc gia." />
        <MetricSummaryCard icon={ThumbsUp} label="Likes đã ghi nhận" value={query.data.length ? totals.likes : "Chưa có dữ liệu"} hint="Dữ liệu lấy từ analytics/sync đã có." />
        <MetricSummaryCard icon={MessageCircle} label="Comments đã ghi nhận" value={query.data.length ? totals.comments : "Chưa có dữ liệu"} hint="Chưa có dữ liệu không phải bằng 0." />
      </div>
      {query.data.length ? (
        <Panel className="overflow-x-auto p-0">
          <table className="w-full min-w-[980px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                {["Tiêu đề", "Nền tảng", "Ngày publish", "Views", "Likes", "Comments", "CTR", "AVD", "Độ mới dữ liệu", "Owner analytics", ""].map((header) => (
                  <th key={header} className="px-4 py-3 font-medium">{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {query.data.map((video) => (
                <tr key={video.id} className="border-b border-border/60">
                  <td className="px-4 py-3 font-medium">{video.title}</td>
                  <td className="px-4 py-3">{video.platform}</td>
                  <td className="px-4 py-3">{new Date(video.published_at).toLocaleDateString("vi-VN")}</td>
                  <td className="px-4 py-3">{video.metrics.views ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3">{video.metrics.likes ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3">{video.metrics.comments ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3">{video.metrics.ctr ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3">{video.metrics.average_view_duration_seconds ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3"><StatusBadge value={video.freshness} /></td>
                  <td className="px-4 py-3"><StatusBadge value={video.owner_analytics_status} /></td>
                  <td className="px-4 py-3">
                    <Button asChild>
                      <Link href={`/uploaded-videos/${video.id}`}>Mở</Link>
                    </Button>
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
          actions={[
            { label: "Đi tới gói publish", href: "/publishing", variant: "primary" },
            { label: "Xem hướng dẫn paste-back", href: "/publishing" }
          ]}
        />
      )}
    </div>
  );
}

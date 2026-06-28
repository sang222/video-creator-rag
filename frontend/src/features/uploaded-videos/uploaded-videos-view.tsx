"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CirclePlay, RefreshCw, Video } from "lucide-react";

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

  const needsPublishConfirmation = query.data.filter((video) => !video.platform_video_id || !video.video_url).length;
  const needsAnalyticsSync = query.data.filter((video) => needsSync(video.freshness) || needsSync(video.owner_analytics_status)).length;
  const waitingVerification = query.data.filter((video) => ["NOT_VERIFIED", "VERIFICATION_UNAVAILABLE", "VERIFICATION_FAILED"].includes(video.verification_status)).length;
  const needsRecovery = query.data.filter((video) => {
    const nextAction = String(video.next_action ?? "").toLowerCase();
    return Boolean(video.latest_diagnostic) || nextAction.includes("recovery") || nextAction.includes("phục hồi");
  }).length;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title="Video đã upload"
        subtitle="Theo dõi video YouTube đã được publish thủ công. Metric chưa có dữ liệu không phải bằng 0."
        breadcrumbs={[{ label: "Trung tâm", href: "/" }, { label: "Video đã upload" }]}
        primaryAction={<Button asChild variant="primary"><Link href="/publishing">Đi tới gói publish</Link></Button>}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={Video} label="Tổng video đã upload" value={query.data.length} hint="Chỉ tính video đã được người vận hành nhập paste-back." />
        <MetricSummaryCard icon={CirclePlay} label="Cần xác nhận publish" value={needsPublishConfirmation} hint="Gói chưa paste-back sẽ nằm ở trang Gói publish." />
        <MetricSummaryCard icon={RefreshCw} label="Chờ xác minh YouTube" value={waitingVerification} hint="VCOS chỉ xác minh read-only khi YouTube đã kết nối." />
        <MetricSummaryCard icon={AlertTriangle} label="Video cần recovery" value={needsRecovery} hint="Chỉ tính video có chẩn đoán hoặc việc tiếp theo là phục hồi." />
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard icon={RefreshCw} label="Analytics cần sync" value={needsAnalyticsSync} hint="Không có dữ liệu không được tính là 0." />
      </div>
      {query.data.length ? (
        <Panel className="overflow-x-auto p-0">
          <table className="w-full min-w-[1220px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                {["Tiêu đề", "YouTube video_id", "Visibility", "Ngày publish", "Views", "Likes", "Comments", "Xác minh", "Analytics", "Độ mới dữ liệu", ""].map((header) => (
                  <th key={header} className="px-4 py-3 font-medium">{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {query.data.map((video) => (
                <tr key={video.id} className="border-b border-border/60">
                  <td className="px-4 py-3 font-medium">{video.title}</td>
                  <td className="px-4 py-3 font-mono text-xs">{video.external_video_id ?? video.platform_video_id}</td>
                  <td className="px-4 py-3"><StatusBadge value={video.actual_visibility ?? "UNKNOWN"} /></td>
                  <td className="px-4 py-3">{new Date(video.published_at).toLocaleDateString("vi-VN")}</td>
                  <td className="px-4 py-3">{video.metrics.views ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3">{video.metrics.likes ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3">{video.metrics.comments ?? "Chưa có dữ liệu"}</td>
                  <td className="px-4 py-3"><StatusBadge value={video.verification_status} /></td>
                  <td className="px-4 py-3"><StatusBadge value={video.analytics_sync_status} /></td>
                  <td className="px-4 py-3"><StatusBadge value={video.freshness} /></td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      <Button asChild>
                        <Link href={`/uploaded-videos/${video.id}`}>Mở</Link>
                      </Button>
                      <Button asChild>
                        <a href={video.external_url ?? video.video_url} target="_blank" rel="noreferrer">Mở YouTube</a>
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
          actions={[
            { label: "Đi tới gói publish", href: "/publishing", variant: "primary" },
            { label: "Xem hướng dẫn paste-back", href: "/publishing" }
          ]}
        />
      )}
    </div>
  );
}

function needsSync(value: string | null | undefined) {
  return ["STALE", "UNKNOWN", "UNAVAILABLE", "NEEDS_AUTH", "FAILED", "NOT_CONFIGURED"].includes(String(value ?? "UNKNOWN").toUpperCase());
}

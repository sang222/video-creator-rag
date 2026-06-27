"use client";

import { useQuery } from "@tanstack/react-query";

import { MetricSummaryCard, PageHeader } from "@/components/cockpit";
import { GoogleDriveMediaCard } from "@/components/google-drive-media-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getUploadedVideoDashboard, queryKeys } from "@/lib/api";

const metricLabels: Record<string, string> = {
  views: "Lượt xem",
  likes: "Lượt thích",
  comments: "Bình luận",
  impressions: "Hiển thị",
  ctr: "CTR",
  average_view_duration_seconds: "AVD",
  average_view_percentage: "Tỷ lệ xem TB",
  estimated_minutes_watched: "Thời gian xem",
  subscribers_gained: "Subscriber tăng",
  subscribers_lost: "Subscriber giảm"
};

export function UploadedVideoDetail({ uploadedVideoId }: { uploadedVideoId: string }) {
  const query = useQuery({
    queryKey: queryKeys.uploadedVideo(uploadedVideoId),
    queryFn: () => getUploadedVideoDashboard(uploadedVideoId)
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Đang tải chi tiết video" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Đang tải chi tiết video" /></div>;

  const data = query.data;
  const videoTitle = String(data.uploaded_video.title ?? "Chi tiết video đã upload");

  return (
    <div className="space-y-6 p-4 md:p-8">
      <PageHeader
        title={videoTitle}
        subtitle="Theo dõi video đã được nhập paste-back sau khi người vận hành publish thủ công trên YouTube."
        breadcrumbs={[{ label: "Video đã upload", href: "/uploaded-videos" }, { label: "Chi tiết video" }]}
      />
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricSummaryCard label="Ngày publish thực tế" value={formatDate(data.uploaded_video.published_at)} hint="Thời điểm do người vận hành nhập sau khi upload thủ công." />
        <MetricSummaryCard label="Khung giờ publish đã cấu hình" value={formatDate(data.publish_check.configured_publish_window)} hint="Không phải khuyến nghị algorithm." />
        <MetricSummaryCard label="Múi giờ kênh" value={String(data.publish_check.channel_timezone ?? "Chưa cấu hình")} hint="Dùng IANA timezone." />
        <MetricSummaryCard label="Giờ tương ứng của operator" value={formatDate(data.publish_check.operator_local_time)} hint="Human vẫn quyết định giờ publish thực tế." />
      </section>
      <section className="grid gap-4 xl:grid-cols-2">
        <MetricPanel title="Thống kê public YouTube" data={data.public_stats} authority="WEAK" />
        <MetricPanel title="Analytics chủ sở hữu" data={data.owner_analytics} authority="STRONG" />
      </section>
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Object.entries(data.publish_check).map(([key, value]) => (
          ["localization_packages", "configured_publish_window", "operator_local_time", "actual_published_at", "channel_timezone", "publish_timing_summary"].includes(key) ? null : (
          <Panel key={key}>
            <div className="text-sm text-muted-foreground">{publishCheckLabel(key)}</div>
            <div className="mt-3"><StatusBadge value={publishCheckStatus(value)} /></div>
          </Panel>
          )
        ))}
      </section>
      <section>
        <Panel>
          <h2 className="text-base font-semibold">Gói phụ đề & metadata theo ngôn ngữ</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {localizationSummary(data.publish_check.localization_packages)}
          </p>
        </Panel>
      </section>
      <section>
        <h2 className="mb-4 text-base font-semibold">Tệp Drive</h2>
        {data.media.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.media.map((media) => <GoogleDriveMediaCard key={media.id} media={media} />)}
          </div>
        ) : (
          <EmptyState title="Chưa có tệp Google Drive" body="File card chỉ xuất hiện sau khi tệp Drive đã được xác minh. Bảng điều hành không hiện đường dẫn local." />
        )}
      </section>
      <section className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <h2 className="text-base font-semibold">Diagnostic / Phục hồi</h2>
          {data.diagnostics.length || data.recovery_proposals.length ? (
            <div className="mt-4 space-y-3 text-sm text-muted-foreground">
              {[...data.diagnostics, ...data.recovery_proposals].map((item, index) => (
                <div key={index} className="rounded-md border border-border p-3">{String(item.operator_summary ?? "Cần xem chẩn đoán")}</div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">Chưa đủ dữ liệu để kết luận.</p>
          )}
        </Panel>
        <Panel>
          <h2 className="text-base font-semibold">Cảnh báo không được làm</h2>
          <div className="mt-4 space-y-3">
            {data.safety_warnings.map((warning) => (
              <div key={warning.key} className="rounded-md border border-amber-500/30 p-3 text-sm text-muted-foreground">
                {warning.text}
              </div>
            ))}
          </div>
        </Panel>
      </section>
    </div>
  );
}

function MetricPanel({ title, data, authority }: { title: string; data: Record<string, unknown>; authority: string }) {
  return (
    <Panel>
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-base font-semibold">{title}</h2>
        <StatusBadge value={authority} />
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {Object.entries(metricLabels).map(([key, label]) => (
          <div key={key} className="rounded-md border border-border bg-background p-3">
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="mt-1 text-lg font-semibold">{data[key] === null || data[key] === undefined ? "Chưa có dữ liệu" : String(data[key])}</div>
          </div>
        ))}
      </div>
      {data.next_action ? <p className="mt-3 text-sm text-amber-100">{String(data.next_action)}</p> : null}
    </Panel>
  );
}

function publishCheckLabel(key: string) {
  return {
    title_match: "Tiêu đề khớp",
    duration_match: "Duration khớp",
    captions: "Caption",
    visibility: "Visibility",
    published_inside_configured_window: "Trong/ngoài khung giờ cấu hình"
  }[key] ?? "Mục kiểm tra";
}

function publishCheckStatus(value: unknown) {
  if (value === true) return "VERIFIED";
  if (value === false) return "NEEDS_HUMAN_REVIEW";
  return String(value ?? "UNKNOWN");
}

function formatDate(value: unknown) {
  if (!value) return "Chưa có dữ liệu";
  return new Date(String(value)).toLocaleString("vi-VN");
}

function localizationSummary(value: unknown) {
  const data = value as { subtitle_languages?: string[]; metadata_languages?: string[] } | undefined;
  const subtitles = data?.subtitle_languages?.length ? data.subtitle_languages.join(", ") : "chưa có";
  const metadata = data?.metadata_languages?.length ? data.metadata_languages.join(", ") : "chưa có";
  return `Phụ đề: ${subtitles}. Metadata theo ngôn ngữ: ${metadata}. Không reupload video này.`;
}

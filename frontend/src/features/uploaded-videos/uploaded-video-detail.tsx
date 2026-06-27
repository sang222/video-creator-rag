"use client";

import { useQuery } from "@tanstack/react-query";

import { GoogleDriveMediaCard } from "@/components/google-drive-media-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Panel } from "@/components/ui/panel";
import { getUploadedVideoDashboard, queryKeys } from "@/lib/api";

const metricLabels: Record<string, string> = {
  views: "Views",
  likes: "Likes",
  comments: "Comments",
  impressions: "Impressions",
  ctr: "CTR",
  average_view_duration_seconds: "AVD",
  average_view_percentage: "Average view %",
  estimated_minutes_watched: "Watch time",
  subscribers_gained: "Subscribers gained",
  subscribers_lost: "Subscribers lost"
};

export function UploadedVideoDetail({ uploadedVideoId }: { uploadedVideoId: string }) {
  const query = useQuery({
    queryKey: queryKeys.uploadedVideo(uploadedVideoId),
    queryFn: () => getUploadedVideoDashboard(uploadedVideoId)
  });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Loading uploaded video" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Loading uploaded video" /></div>;

  const data = query.data;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">{String(data.uploaded_video.platform_video_id)}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{String(data.uploaded_video.video_url)}</p>
      </div>
      <section className="grid gap-4 xl:grid-cols-2">
        <MetricPanel title="Public YouTube Stats" data={data.public_stats} authority="WEAK" />
        <MetricPanel title="Owner Analytics" data={data.owner_analytics} authority="STRONG" />
      </section>
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Object.entries(data.publish_check).map(([key, value]) => (
          <Panel key={key}>
            <div className="text-sm text-muted-foreground">{key.replaceAll("_", " ")}</div>
            <div className="mt-3"><StatusBadge value={String(value)} /></div>
          </Panel>
        ))}
      </section>
      <section>
        <h2 className="mb-4 text-base font-semibold">Media / Google Drive</h2>
        {data.media.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.media.map((media) => <GoogleDriveMediaCard key={media.id} media={media} />)}
          </div>
        ) : (
          <EmptyState title="No Drive media refs" body="File cards appear only after M10.5 CloudMediaRef verification." />
        )}
      </section>
      <section className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <h2 className="text-base font-semibold">Diagnostics / Recovery</h2>
          {data.diagnostics.length || data.recovery_proposals.length ? (
            <div className="mt-4 space-y-3 text-sm text-muted-foreground">
              {[...data.diagnostics, ...data.recovery_proposals].map((item, index) => (
                <div key={index} className="rounded-md border border-border p-3">{String(item.operator_summary ?? item.primary_status ?? item.proposal_type)}</div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">Chưa đủ dữ liệu để kết luận.</p>
          )}
        </Panel>
        <Panel>
          <h2 className="text-base font-semibold">Do-not-do Warnings</h2>
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
            <div className="mt-1 text-lg font-semibold">{data[key] === null || data[key] === undefined ? "Unknown" : String(data[key])}</div>
          </div>
        ))}
      </div>
      {data.next_action ? <p className="mt-3 text-sm text-amber-100">{String(data.next_action)}</p> : null}
    </Panel>
  );
}

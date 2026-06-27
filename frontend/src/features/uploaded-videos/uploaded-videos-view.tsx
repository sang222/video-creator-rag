"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { getUploadedVideos, queryKeys } from "@/lib/api";

export function UploadedVideosView() {
  const query = useQuery({ queryKey: queryKeys.uploadedVideos, queryFn: getUploadedVideos });

  if (query.isLoading) return <div className="p-4 md:p-8"><LoadingState label="Loading uploaded videos" /></div>;
  if (query.isError) return <div className="p-4 md:p-8"><ErrorState message={query.error.message} /></div>;
  if (!query.data) return <div className="p-4 md:p-8"><LoadingState label="Loading uploaded videos" /></div>;

  return (
    <div className="space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Uploaded Videos</h1>
        <p className="mt-1 text-sm text-muted-foreground">Unknown owner metrics stay unknown. They are not zero.</p>
      </div>
      {query.data.length ? (
        <Panel className="overflow-x-auto p-0">
          <table className="w-full min-w-[980px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                {["Title", "Platform", "Published", "Views", "Likes", "Comments", "CTR", "AVD", "Freshness", "Owner analytics", ""].map((header) => (
                  <th key={header} className="px-4 py-3 font-medium">{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {query.data.map((video) => (
                <tr key={video.id} className="border-b border-border/60">
                  <td className="px-4 py-3 font-medium">{video.title}</td>
                  <td className="px-4 py-3">{video.platform}</td>
                  <td className="px-4 py-3">{new Date(video.published_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3">{video.metrics.views ?? "Unknown"}</td>
                  <td className="px-4 py-3">{video.metrics.likes ?? "Unknown"}</td>
                  <td className="px-4 py-3">{video.metrics.comments ?? "Unknown"}</td>
                  <td className="px-4 py-3">{video.metrics.ctr ?? "Unknown"}</td>
                  <td className="px-4 py-3">{video.metrics.average_view_duration_seconds ?? "Unknown"}</td>
                  <td className="px-4 py-3"><StatusBadge value={video.freshness} /></td>
                  <td className="px-4 py-3"><StatusBadge value={video.owner_analytics_status} /></td>
                  <td className="px-4 py-3">
                    <Button asChild>
                      <Link href={`/uploaded-videos/${video.id}`}>Open</Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : (
        <EmptyState title="No uploaded videos" body="Manual publish confirmations create UploadedVideo records after human paste-back." />
      )}
    </div>
  );
}
